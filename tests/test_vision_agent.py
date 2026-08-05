"""Task 8：视觉 Agent 测试（注入 stub 绕过权重依赖）。"""
from agents.base import AgentMessage
from agents.vision_agent import VisionAgent


class _StubYolo:
    session = True  # 仅占位，避免 VisionAgent 尝试从 config 加载真实权重
    conf_thres = 0.45  # 占位，用于 fire_model_limitation 提示

    def infer(self, p):
        return [{"cls": "spark", "conf": 0.9, "bbox": [1, 2, 3, 4]}]


def test_vision_agent_success():
    m = AgentMessage(
        task_id="t1", agent="vision", status="pending",
        payload={"image_paths": ["a.jpg", "b.jpg"]}, error=None, cost_ms=0)
    out = VisionAgent(yolo=_StubYolo()).run(m)
    assert out.status == "success"
    assert len(out.payload["detections"]) == 2
    assert out.payload["detections"][0]["violation_desc"] == "火花（动火明火）"
    # violation_descs 按类别去重，避免同一类多个检测框刷屏
    assert out.payload["violation_descs"] == ["火花（动火明火）"]
    # 检出动火专用类，不应标记能力限制
    assert out.payload.get("fire_model_limitation") is None
    assert out.cost_ms >= 0


def test_vision_agent_empty_images():
    m = AgentMessage(
        task_id="t2", agent="vision", status="pending",
        payload={"image_paths": []}, error=None, cost_ms=0)
    out = VisionAgent(yolo=_StubYolo()).run(m)
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
    m = AgentMessage(
        task_id="t4", agent="vision", status="pending",
        payload={"image_paths": ["scene.jpg"]}, error=None, cost_ms=0)
    out = VisionAgent(yolo=_StubYoloCoco()).run(m)
    assert out.status == "success"
    assert out.payload["detections"][0]["violation_desc"]  # COCO_CN 映射
    assert out.payload.get("fire_model_limitation")  # 非动火类→说明


def test_vision_agent_missing_weight_failed(monkeypatch):
    # 无注入且权重加载失败 -> 异常被基类转 failed（不崩溃）
    import core.yolo_engine as ye

    def _boom(self, path):
        raise FileNotFoundError(f"forced missing weight: {path}")

    monkeypatch.setattr(ye.YoloEngine, "load", _boom)
    m = AgentMessage(
        task_id="t3", agent="vision", status="pending",
        payload={"image_paths": ["x.jpg"]}, error=None, cost_ms=0)
    out = VisionAgent().run(m)
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

    out = VisionAgent(yolo=_StubSafeYolo()).run(AgentMessage(
        task_id="t_safe", agent="vision", status="pending",
        payload={"image_paths": ["x.jpg"]}, error=None, cost_ms=0))
    assert out.status == "success"
    assert out.payload["violation_descs"] == []
