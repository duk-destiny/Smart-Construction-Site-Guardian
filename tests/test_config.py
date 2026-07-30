"""Task 1：配置加载模块测试（TDD 先写测试，再实现 core/config.py）。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.config import ConfigError, ConfigLoader


def test_load_returns_expected_keys():
    cfg = ConfigLoader("config/config.yaml").load()
    assert cfg["models"]["yolo_onnx"].endswith(".onnx")
    assert "hot_work" in cfg["scenes"]


def test_get_scene_returns_hotwork():
    scene = ConfigLoader().get_scene("hot_work")
    assert scene["kb_collection"] == "kb_hot_work"
    assert scene["risk_matrix"].endswith(".yaml")


def test_get_dotted_path():
    cfg = ConfigLoader()
    assert cfg.get("infer.conf_thres") == 0.45
    assert cfg.get("not.exists", "fallback") == "fallback"


def test_get_scene_unknown_raises():
    with __import__("pytest").raises(ConfigError):
        ConfigLoader().get_scene("nonexistent")
