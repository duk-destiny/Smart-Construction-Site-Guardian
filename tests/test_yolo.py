"""Task 7：YOLO 引擎测试（decode 用合成张量，加载缺失权重验证契约）。"""
import numpy as np
import pytest

from core.yolo_engine import YoloEngine


def test_decode_filters_low_conf_and_whitelist():
    eng = YoloEngine(conf_thres=0.45)
    # raw: [1, 10, 2] -> 4 box + 6 class, 2 anchors
    raw = np.zeros((1, 10, 2), dtype=np.float32)
    # anchor0: cls0 conf 0.9 -> WHITELIST[0]=spark（白名单映射）
    raw[0, 0, 0], raw[0, 1, 0], raw[0, 2, 0], raw[0, 3, 0] = 10, 20, 30, 40
    raw[0, 4, 0] = 0.9
    # anchor1: 同类 cls0 但置信度 0.1 < 阈值 -> 被过滤
    raw[0, 0, 1], raw[0, 1, 1], raw[0, 2, 1], raw[0, 3, 1] = 1, 2, 3, 4
    raw[0, 4, 1] = 0.1
    dets = eng._decode(raw)
    assert len(dets) == 1
    assert dets[0]["cls"] == "spark"
    assert dets[0]["conf"] == 0.9
    assert dets[0]["bbox"][:2] == [10.0, 20.0]


def test_decode_drops_below_threshold():
    eng = YoloEngine(conf_thres=0.5)
    raw = np.zeros((1, 8, 1), dtype=np.float32)
    raw[0, 4, 0] = 0.3  # cls0 conf 0.3 < 0.5
    assert eng._decode(raw) == []


def test_load_missing_weight_raises():
    eng = YoloEngine()
    with pytest.raises(FileNotFoundError):
        eng.load("data/models/__not_exist__.onnx")


def test_infer_without_load_raises():
    eng = YoloEngine()
    with pytest.raises(RuntimeError):
        eng.infer("x.jpg")


def test_decode_maps_model_classes_via_class_map():
    """数据驱动：模型类名经 class_map 映射到项目隐患键，None 类丢弃。"""
    eng = YoloEngine(conf_thres=0.45,
                     class_map={"Fire": "spark", "default": None, "smoke": "smoke"})
    eng.class_names = ["Fire", "default", "smoke"]
    # raw: [1, 7, 2] -> 4 box + 3 class, 2 anchors
    raw = np.zeros((1, 7, 2), dtype=np.float32)
    raw[0, 4, 0] = 0.9    # Fire -> spark
    raw[0, 6, 1] = 0.8    # cls2 smoke -> smoke
    dets = eng._decode(raw)
    assert {d["cls"] for d in dets} == {"spark", "smoke"}
    assert all(d["cls"] != "default" for d in dets)


def test_load_real_fire_onnx_parses_names():
    """加载真实火情 ONNX，验证类别名从元数据正确解析并映射。"""
    import os
    onnx = os.path.join("data", "models", "yolov8_fire_smoke_v2.onnx")
    if not os.path.exists(onnx):
        pytest.skip("火情 ONNX 未导出，跳过")
    eng = YoloEngine(conf_thres=0.45,
                     class_map={"spark": "spark", "smoke": "smoke",
                                "extinguisher": "extinguisher"})
    eng.load(onnx)
    assert eng.class_names == ["spark", "smoke", "extinguisher"]
