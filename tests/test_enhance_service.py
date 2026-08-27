"""AI 提取预填服务测试（v0.6 二期a）：双 Provider 降级链 + 白名单硬校验。

不触网：云端通道 monkeypatch _chat_cloud / 本地 monkeypatch LlmEngine；
重点验证链序（auto=云→本地）、越白名单弃包、safe 信号拒绝、空输入。
"""
from __future__ import annotations

import pytest

from services.enhance_service import EnhanceEngine


GOOD = {"hazard_key": "flammable", "scene_id": "hot_work",
        "description": "纸箱堆放", "location": "3号楼西侧"}


def test_unconfigured_returns_none_and_silent(monkeypatch):
    eng = EnhanceEngine(provider="auto")
    monkeypatch.setattr(eng, "_cloud_ok", lambda: False)
    monkeypatch.setattr(eng, "_local_ok", lambda: False)
    assert eng.available() is None
    assert eng.extract_hazard("随便写点") is None


def test_cloud_provider_used_when_configured(monkeypatch):
    eng = EnhanceEngine(provider="cloud")
    eng.cloud_base, eng.cloud_key = "https://x/v1", "k"
    monkeypatch.setattr(eng, "_chat_cloud", lambda s, u: dict(GOOD))
    assert eng.available() == "cloud"
    out = eng.extract_hazard("西侧纸箱")
    assert out == GOOD


def test_auto_falls_cloud_to_local(monkeypatch):
    eng = EnhanceEngine(provider="auto")
    eng.cloud_base, eng.cloud_key = "https://x/v1", "k"
    monkeypatch.setattr(eng, "_chat_cloud", lambda s, u: None)  # 云端失败
    monkeypatch.setattr(eng, "_chat_local", lambda s, u: dict(GOOD))
    monkeypatch.setattr(eng, "_local_ok", lambda: True)          # 本地在线
    out = eng.extract_hazard("西侧纸箱")
    assert out == GOOD
    assert "cloud" in (eng.last_error or "") or eng.last_error is None


def test_whitelist_rejects_unknown_key(monkeypatch):
    eng = EnhanceEngine(provider="cloud")
    eng.cloud_base, eng.cloud_key = "https://x/v1", "k"
    monkeypatch.setattr(eng, "_chat_cloud",
                        lambda s, u: dict(GOOD, hazard_key="ufo"))
    assert eng.extract_hazard("文本") is None
    assert "越白名单" in (eng.last_error or "")


def test_whitelist_rejects_safe_signal(monkeypatch):
    eng = EnhanceEngine(provider="cloud")
    eng.cloud_base, eng.cloud_key = "https://x/v1", "k"
    monkeypatch.setattr(eng, "_chat_cloud",
                        lambda s, u: dict(GOOD, hazard_key="helmet"))
    assert eng.extract_hazard("都戴了帽子") is None


def test_empty_input_short_circuit():
    eng = EnhanceEngine(provider="cloud")
    assert eng.extract_hazard("  ") is None
    assert eng.last_error == "空输入"
