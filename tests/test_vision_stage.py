"""Task 8：视觉 Agent 测试（注入 stub 绕过权重依赖）；T6 增补缓存语义。"""
import time

from pipeline.base import StageMessage
from pipeline.detection_cache import DetectionCache
from pipeline.vision import VisionStage


class _StubYolo:
    session = True  # 仅占位，避免 VisionStage 尝试从 config 加载真实权重
    conf_thres = 0.45  # 占位，用于 fire_model_limitation 提示

    def infer(self, p):
        return [{"cls": "spark", "conf": 0.9, "bbox": [1, 2, 3, 4]}]


def test_vision_agent_success():
    m = StageMessage(
        task_id="t1", agent="vision", status="pending",
        payload={"image_paths": ["a.jpg", "b.jpg"]}, error=None, cost_ms=0)
    out = VisionStage(yolo=_StubYolo()).run(m)
    assert out.status == "success"
    assert len(out.payload["detections"]) == 2
    assert out.payload["detections"][0]["violation_desc"] == "火花（动火明火）"
    # violation_descs 按类别去重，避免同一类多个检测框刷屏
    assert out.payload["violation_descs"] == ["火花（动火明火）"]
    # 检出动火专用类，不应标记能力限制
    assert out.payload.get("fire_model_limitation") is None
    assert out.cost_ms >= 0


def test_vision_agent_empty_images():
    m = StageMessage(
        task_id="t2", agent="vision", status="pending",
        payload={"image_paths": []}, error=None, cost_ms=0)
    out = VisionStage(yolo=_StubYolo()).run(m)
    assert out.status == "success"
    assert out.payload["detections"] == []


class _StubYoloCoco:
    """模拟标准 COCO 权重：检出 person，无动火专用 4 类。"""
    session = True
    conf_thres = 0.45  # 占位，用于 fire_model_limitation 提示

    def infer(self, p):
        return [{"cls": "person", "conf": 0.88, "bbox": [1, 2, 3, 4]}]


def test_vision_agent_non_fire_note():
    """检出非动火类目标（如人员）→ 附加未检出明火/烟雾的说明（诚实降级）。"""
    m = StageMessage(
        task_id="t4", agent="vision", status="pending",
        payload={"image_paths": ["scene.jpg"]}, error=None, cost_ms=0)
    out = VisionStage(yolo=_StubYoloCoco()).run(m)
    assert out.status == "success"
    assert out.payload["detections"][0]["violation_desc"]  # COCO_CN 映射
    assert out.payload.get("fire_model_limitation")  # 非动火类→说明


def test_vision_agent_missing_weight_failed(monkeypatch):
    # 无注入且权重加载失败 -> 异常被基类转 failed（不崩溃）
    import core.yolo_engine as ye

    def _boom(self, path):
        raise FileNotFoundError(f"forced missing weight: {path}")

    monkeypatch.setattr(ye.YoloEngine, "load", _boom)
    m = StageMessage(
        task_id="t3", agent="vision", status="pending",
        payload={"image_paths": ["x.jpg"]}, error=None, cost_ms=0)
    out = VisionStage().run(m)
    assert out.status == "failed"
    assert out.error


def test_safe_signals_not_in_violation_descs():
    class _StubSafeYolo:
        session = True
        conf_thres = 0.45
        def infer(self, p):
            return [
                {"cls": "helmet", "conf": 0.9, "bbox": [1, 2, 3, 4]},
                {"cls": "vest", "conf": 0.9, "bbox": [1, 2, 3, 4]},
                {"cls": "person", "conf": 0.9, "bbox": [1, 2, 3, 4]},
            ]

    out = VisionStage(yolo=_StubSafeYolo()).run(StageMessage(
        task_id="t_safe", agent="vision", status="pending",
        payload={"image_paths": ["x.jpg"]}, error=None, cost_ms=0))
    assert out.status == "success"
    assert out.payload["violation_descs"] == []


# ---------- T6：DetectionCache 缓存语义（§5.10，默认关闭）----------

class _CountingYolo:
    session = True
    conf_thres = 0.45

    def __init__(self):
        self.calls = 0

    def infer(self, p):
        self.calls += 1
        return [{"cls": "spark", "conf": 0.9, "bbox": [1, 2, 3, 4]}]


def _vision_msg(path):
    return StageMessage(
        task_id="t_cache", agent="vision", status="pending",
        payload={"image_paths": [path]}, error=None, cost_ms=0)


def test_vision_agent_cache_none_default_unchanged(tmp_path):
    """cache 默认 None=关闭：同文件重复调用每次都真跑推理（主链路零影响）。"""
    f = tmp_path / "frame.jpg"
    f.write_bytes(b"frame-bytes")
    yolo = _CountingYolo()
    agent = VisionStage(yolo=yolo)
    assert agent.cache is None
    out1 = agent.run(_vision_msg(str(f)))
    out2 = agent.run(_vision_msg(str(f)))
    assert yolo.calls == 2
    assert out1.status == "success" and out2.status == "success"


def test_vision_agent_cache_hit_skips_infer(tmp_path):
    """注入缓存后：同内容文件第二次命中缓存，不再调用检测。"""
    f = tmp_path / "frame.jpg"
    f.write_bytes(b"frame-bytes")
    yolo = _CountingYolo()
    cache = DetectionCache()
    agent = VisionStage(yolo=yolo, cache=cache)
    out1 = agent.run(_vision_msg(str(f)))
    out2 = agent.run(_vision_msg(str(f)))
    assert yolo.calls == 1                       # 第二次命中，跳过推理
    assert out2.status == "success"
    assert out1.payload["detections"] == out2.payload["detections"]
    assert out2.payload["violation_descs"] == ["火花（动火明火）"]
    # 不同内容 → 新 key，不命中
    f2 = tmp_path / "frame2.jpg"
    f2.write_bytes(b"other-bytes")
    agent.run(_vision_msg(str(f2)))
    assert yolo.calls == 2


def test_vision_agent_cache_ttl_expiry(tmp_path):
    """TTL 过期后重新检测（会话生命周期失效）。"""
    f = tmp_path / "frame.jpg"
    f.write_bytes(b"frame-bytes")
    yolo = _CountingYolo()
    agent = VisionStage(yolo=yolo, cache=DetectionCache(ttl_sec=0.05))
    agent.run(_vision_msg(str(f)))
    assert yolo.calls == 1
    time.sleep(0.08)
    out = agent.run(_vision_msg(str(f)))
    assert yolo.calls == 2                       # 过期逐出，重新推理
    assert out.status == "success"


def test_vision_agent_cache_unreadable_file_falls_back(tmp_path):
    """文件不可读（无法算 hash）时不走缓存，直接推理，不崩。"""
    yolo = _CountingYolo()
    agent = VisionStage(yolo=yolo, cache=DetectionCache())
    missing = str(tmp_path / "missing.jpg")
    out = agent.run(_vision_msg(missing))
    assert out.status == "success"
    assert yolo.calls == 1


def test_detection_cache_key_of_file(tmp_path):
    """key=内容 sha256 前 16 位：同内容同 key，重新上传（新内容）新 key。"""
    a = tmp_path / "a.jpg"
    b = tmp_path / "b.jpg"
    c = tmp_path / "c.jpg"
    a.write_bytes(b"same")
    b.write_bytes(b"same")
    c.write_bytes(b"different")
    ka, kb, kc = (DetectionCache.key_of_file(str(p)) for p in (a, b, c))
    assert ka == kb and ka != kc and len(ka) == 16
    assert DetectionCache.key_of_file(str(tmp_path / "missing")) is None


def test_detection_cache_capacity_evicts_lru():
    """容量上限：逐出最久未访问项，近期访问过的保留（LRU）。"""
    cache = DetectionCache(capacity=2)
    cache.put("a", 1)
    cache.put("b", 2)
    assert cache.get("a") == 1                   # a 变最近访问
    cache.put("c", 3)                            # 超限 → 逐出 b（最久未访问）
    assert cache.get("b") is None
    assert cache.get("a") == 1
    assert cache.get("c") == 3
    assert len(cache) == 2


def test_detection_cache_ttl_and_purge():
    """TTL 过期 get 返 None；不落盘（纯内存结构）。"""
    cache = DetectionCache(ttl_sec=0.05)
    cache.put("k", [{"cls": "spark"}])
    assert cache.get("k") == [{"cls": "spark"}]
    time.sleep(0.08)
    assert cache.get("k") is None
    cache.put("k2", "v")
    cache.purge_expired()
    assert len(cache) == 1
