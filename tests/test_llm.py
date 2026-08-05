"""LLM 引擎测试（TDD：用 mock 隔离 Ollama，保证确定性 + 快速）。"""

import json
from unittest import mock

from core.llm_engine import LlmEngine


def _resp(obj):
    """返回可进入 with 的假响应对象。"""
    class _R:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self):
            return json.dumps(obj).encode("utf-8")
    return _R()


def _tags_resp():
    return _resp({"models": [{"name": "qwen3:8b"}]})


def _chat_resp(text):
    return _resp({"message": {"content": text}})


def test_available_true_when_model_present():
    """Ollama 返回含 qwen3:8b 时 available()=True。"""
    with mock.patch("urllib.request.urlopen", side_effect=lambda *a, **k: _tags_resp()):
        eng = LlmEngine(base_url="http://localhost:11434", model="qwen3:8b")
        assert eng.available() is True


def test_available_false_when_ollama_down():
    """urlopen 抛异常 → available()=False（降级）。"""
    with mock.patch("urllib.request.urlopen", side_effect=OSError("conn refused")):
        eng = LlmEngine()
        assert eng.available() is False


def test_polish_returns_text_when_available():
    """可用时 polish 走 /api/chat、带 think=false 与 keep_alive，并返回响应文本。"""
    captured = []

    def _fake(req, *a, **k):
        captured.append(req)
        return _chat_resp("请立即停工")

    with mock.patch("urllib.request.urlopen", side_effect=_fake):
        eng = LlmEngine()
        out = eng.polish("提醒工人注意火灾")
        assert out == "请立即停工"
        assert captured and captured[-1].full_url.endswith("/api/chat")
        body = json.loads(captured[-1].data)
        assert body["think"] is False
        assert body["keep_alive"]  # 常驻防冷启


def test_warmup_best_effort_and_idempotent():
    """warmup 进程内只跑一次、失败静默、不抛异常。"""
    import core.llm_engine as mod
    mod.LlmEngine._warmed = False
    calls = {"n": 0}

    def _fake(req, *a, **k):
        calls["n"] += 1
        return _chat_resp("ok")

    with mock.patch("urllib.request.urlopen", side_effect=_fake):
        eng = LlmEngine()
        eng.warmup()
        eng.warmup()  # 已预热，不应再发请求
    assert calls["n"] == 1
    mod.LlmEngine._warmed = False  # 复位，避免影响其他测试


def test_polish_none_on_failure():
    """生成异常 → polish=None（触发模板降级）。"""
    with mock.patch("urllib.request.urlopen", side_effect=OSError("timeout")):
        eng = LlmEngine()
        assert eng.polish("x") is None


def test_disabled_by_config():
    """config llm.enabled=false → available()=False，不发请求。"""
    import core.config as cc
    fake_cfg = {"llm": {"enabled": False, "base_url": "http://x", "model": "m"}}
    with mock.patch.object(cc.ConfigLoader, "load", return_value=fake_cfg):
        eng = LlmEngine()
        assert eng.available() is False
