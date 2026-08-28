"""AI 提取预填服务测试（v0.6 双 Provider → v0.8 多 Provider 链）。

不触网：provider 调用统一 monkeypatch `_call`/`_chat_cloud`/`_chat_local`，
本地探活 monkeypatch `core.llm_engine.LlmEngine`。
重点验证：链序与降级（列表序=降级序、legacy 单槽合成等价链）、
逐家白名单硬校验（越界即弃试下一家）、总预算止血、连通性自检、
通用 chat（测试场按 base 润色）。
"""
from __future__ import annotations

import urllib.error

import pytest

import services.enhance_service as es
from services.enhance_service import EnhanceEngine

GOOD = {"hazard_key": "flammable", "scene_id": "hot_work",
        "description": "纸箱堆放", "location": "3号楼西侧"}

CLOUD = {"name": "cloud", "type": "cloud", "api_base": "https://x/v1",
         "api_key": "k", "model": "m", "timeout_sec": 20}
LOCAL = {"name": "local", "type": "local", "api_base": "",
         "api_key": "", "model": "", "timeout_sec": 20}


class FakeLlm:
    """core.llm_engine.LlmEngine 替身：探活与 chat 可控，不触网。"""

    enabled = True
    _enabled = True
    alive = True
    model = "fake-model"
    chats: list = []

    def __init__(self, model=None, **kw):
        self.model = model or FakeLlm.model

    def available(self):
        return FakeLlm.alive

    def chat(self, system, user, num_predict=None):
        FakeLlm.chats.append((system, user, num_predict))
        return "润色结果"

    def ask_json(self, text):
        return None


@pytest.fixture(autouse=True)
def _reset_fake():
    FakeLlm.enabled = True
    FakeLlm._enabled = True
    FakeLlm.alive = True
    FakeLlm.chats = []


# ---------- legacy 单槽合成（v0.6 兼容）----------

def test_legacy_auto_synthesizes_cloud_then_local(monkeypatch):
    cfg = {"provider": "auto",
           "cloud": {"api_base": "https://x/v1", "api_key": "k",
                     "model": "m"}}
    chain = EnhanceEngine._normalize(cfg)
    assert [p["type"] for p in chain] == ["cloud", "local"]
    assert chain[0]["name"] == "cloud"


def test_legacy_mode_filters(monkeypatch):
    cfg_cloud = {"provider": "cloud", "cloud": {"api_base": "h", "api_key": "k"}}
    assert [p["type"] for p in EnhanceEngine._normalize(cfg_cloud)] == ["cloud"]
    cfg_local = {"provider": "local"}
    assert [p["type"] for p in EnhanceEngine._normalize(cfg_local)] == ["local"]
    # cloud 配置不完整 → 整条丢弃（宁缺毋错）
    cfg_bad = {"provider": "cloud", "cloud": {"api_base": "h"}}
    assert EnhanceEngine._normalize(cfg_bad) == []


def test_legacy_unconfigured_silent(monkeypatch):
    eng = EnhanceEngine(provider="auto")
    monkeypatch.setattr(eng, "providers", [])
    assert eng.available() is None
    assert eng.extract_hazard("随便写点") is None
    assert "无可用 Provider" in (eng.last_error or "")


# ---------- providers 列表（v0.8 多 base）----------

def test_providers_list_normalization():
    cfg = {"providers": [
        {"name": "deepseek", "api_base": "https://a/v1", "api_key": "k1",
         "model": "deepseek-chat"},
        {"api_base": "https://b/v1", "api_key": "k2"},        # 缺 model → 丢弃
        {"name": "local", "model": "qwen3:8b"},               # 按名推断 local
        {"name": "ghost", "type": "cloud"},                   # 缺 key → 丢弃
        "junk",                                               # 非法条目跳过
    ]}
    chain = EnhanceEngine._normalize(cfg)
    assert [p["name"] for p in chain] == ["deepseek", "local"]
    assert chain[0]["model"] == "deepseek-chat"
    assert chain[1]["type"] == "local"


def test_providers_list_overrides_legacy_slot():
    cfg = {"provider": "auto",
           "cloud": {"api_base": "https://legacy/v1", "api_key": "k",
                     "model": "old"},
           "providers": [{"name": "deepseek", "api_base": "https://a/v1",
                          "api_key": "k1", "model": "deepseek-chat"}]}
    chain = EnhanceEngine._normalize(cfg)
    assert [p["name"] for p in chain] == ["deepseek"]   # 列表优先，legacy 不再合成


def test_chain_mode_filters_provider_list():
    eng = EnhanceEngine(provider="cloud")
    eng.providers = [dict(CLOUD), dict(LOCAL)]
    assert [p["name"] for p in eng.chain()] == ["cloud"]
    eng2 = EnhanceEngine(provider="local")
    eng2.providers = [dict(CLOUD), dict(LOCAL)]
    assert [p["name"] for p in eng2.chain()] == ["local"]


def test_available_prefers_cloud_then_checks_local(monkeypatch):
    eng = EnhanceEngine()
    eng.providers = [dict(CLOUD), dict(LOCAL)]
    assert eng.available() == "cloud"                    # 云配置即用
    eng.providers = [dict(LOCAL)]
    monkeypatch.setattr("core.llm_engine.LlmEngine", FakeLlm)
    assert eng.available() == "local"
    FakeLlm.alive = False
    assert eng.available() is None


# ---------- 提取链路与白名单 ----------

def test_cloud_provider_used_when_configured(monkeypatch):
    eng = EnhanceEngine(provider="cloud")
    eng.providers = [dict(CLOUD)]
    monkeypatch.setattr(eng, "_chat_cloud",
                        lambda p, s, u, timeout=None: dict(GOOD))
    assert eng.available() == "cloud"
    out = eng.extract_hazard("西侧纸箱")
    assert out == GOOD


def test_auto_falls_cloud_to_local(monkeypatch):
    eng = EnhanceEngine(provider="auto")
    eng.providers = [dict(CLOUD), dict(LOCAL)]

    def _cloud_fail(p, s, u, timeout=None):
        eng.last_error = f"[{p['name']}] fail"    # 模拟真实 _chat_cloud 留错
        return None

    monkeypatch.setattr(eng, "_chat_cloud", _cloud_fail)
    monkeypatch.setattr(eng, "_chat_local",
                        lambda p, s, u: dict(GOOD))
    out = eng.extract_hazard("西侧纸箱")
    assert out == GOOD
    assert "[cloud]" in (eng.last_error or "")                # 失败留因


def test_multi_provider_failover(monkeypatch):
    """三家链：p1 失败 → p2 命中（不再落到 local）。"""
    eng = EnhanceEngine()
    p1 = dict(CLOUD, name="p1")
    p2 = dict(CLOUD, name="p2", api_base="https://y/v1")
    eng.providers = [p1, p2, dict(LOCAL)]
    seen = []

    def fake_call(p, system, user, timeout=None):
        seen.append(p["name"])
        if p["name"] == "p2":
            return dict(GOOD)
        eng.last_error = f"[{p['name']}] fail"    # 模拟真实留错
        return None

    monkeypatch.setattr(eng, "_call", fake_call)
    out = eng.extract_hazard("西侧纸箱")
    assert out == GOOD and seen == ["p1", "p2"]
    assert "[p1]" in (eng.last_error or "")


def test_whitelist_reject_then_next_provider(monkeypatch):
    """p1 越白名单 → 弃包试 p2；错误信息带 provider 名可定位。"""
    eng = EnhanceEngine()
    eng.providers = [dict(CLOUD, name="p1"), dict(CLOUD, name="p2")]

    def fake_call(p, system, user, timeout=None):
        if p["name"] == "p1":
            return dict(GOOD, hazard_key="ufo")
        return dict(GOOD)

    monkeypatch.setattr(eng, "_call", fake_call)
    out = eng.extract_hazard("文本")
    assert out == GOOD
    assert "[p1]" in (eng.last_error or "") and "越白名单" in eng.last_error


def test_whitelist_rejects_unknown_key(monkeypatch):
    eng = EnhanceEngine(provider="cloud")
    eng.providers = [dict(CLOUD)]
    monkeypatch.setattr(eng, "_chat_cloud",
                        lambda p, s, u, timeout=None: dict(GOOD, hazard_key="ufo"))
    assert eng.extract_hazard("文本") is None
    assert "越白名单" in (eng.last_error or "")


def test_whitelist_rejects_safe_signal(monkeypatch):
    eng = EnhanceEngine(provider="cloud")
    eng.providers = [dict(CLOUD)]
    monkeypatch.setattr(eng, "_chat_cloud",
                        lambda p, s, u, timeout=None: dict(GOOD, hazard_key="helmet"))
    assert eng.extract_hazard("都戴了帽子") is None


def test_empty_input_short_circuit():
    eng = EnhanceEngine(provider="cloud")
    assert eng.extract_hazard("  ") is None
    assert eng.last_error == "空输入"


def test_total_deadline_stops_chain(monkeypatch):
    """全链总预算耗尽即止血：不再发起任何 provider 调用。"""
    eng = EnhanceEngine()
    eng.total_deadline_sec = -1.0        # 强制超预算
    eng.providers = [dict(CLOUD), dict(LOCAL)]
    calls = []

    monkeypatch.setattr(eng, "_call",
                        lambda p, s, u, timeout=None: calls.append(p["name"]))
    assert eng.extract_hazard("西侧纸箱") is None
    assert calls == []
    assert "总预算" in (eng.last_error or "")


# ---------- 通用 chat（测试场按 base 润色）----------

def test_chat_local_dispatch(monkeypatch):
    eng = EnhanceEngine()
    eng.providers = [dict(LOCAL)]
    monkeypatch.setattr("core.llm_engine.LlmEngine", FakeLlm)
    assert eng.chat("local", "sys", "user") == "润色结果"
    assert FakeLlm.chats and FakeLlm.chats[0][0] == "sys"


def test_chat_unknown_provider():
    eng = EnhanceEngine()
    eng.providers = [dict(LOCAL)]
    assert eng.chat("ghost", "s", "u") is None
    assert "未知 provider" in (eng.last_error or "")


def test_chat_cloud_returns_text(monkeypatch):
    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return '{"choices":[{"message":{"content":"云端润色"}}]}'.encode("utf-8")

    eng = EnhanceEngine()
    eng.providers = [dict(CLOUD)]
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: _Resp())
    assert eng.chat("cloud", "sys", "user") == "云端润色"


# ---------- 连通性自检 ----------

def test_check_provider_local_states(monkeypatch):
    eng = EnhanceEngine()
    eng.providers = [dict(LOCAL)]
    monkeypatch.setattr("core.llm_engine.LlmEngine", FakeLlm)
    r = eng.check_provider(eng.providers[0])
    assert r["ok"] and r["status"] == "ok" and "可调用" in r["detail"]
    FakeLlm.alive = False
    r = eng.check_provider(eng.providers[0])
    assert not r["ok"] and "不可达" in r["detail"]
    FakeLlm._enabled = False
    r = eng.check_provider(eng.providers[0])
    assert r["ok"] and r["status"] == "disabled"


def test_check_provider_cloud_unreachable(monkeypatch):
    eng = EnhanceEngine()
    eng.providers = [dict(CLOUD)]

    def fake(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", None, None)

    monkeypatch.setattr("urllib.request.urlopen", fake)
    r = eng.check_provider(eng.providers[0])
    assert not r["ok"] and "key 无效" in r["detail"]


def test_check_cloud_compat_without_cloud():
    """legacy 兼容入口：链上无云 provider 时返回 unconfigured。"""
    eng = EnhanceEngine()
    eng.providers = [dict(LOCAL)]
    r = eng.check_cloud()
    assert r["status"] == "unconfigured"


def test_check_all_covers_chain(monkeypatch):
    eng = EnhanceEngine()
    eng.providers = [dict(CLOUD), dict(LOCAL)]
    monkeypatch.setattr("core.llm_engine.LlmEngine", FakeLlm)

    def fake(req, timeout=None):
        raise urllib.error.URLError("refused")

    monkeypatch.setattr("urllib.request.urlopen", fake)
    results = eng.check_all()
    assert [r["name"] for r in results] == ["cloud", "local"]
    assert not results[0]["ok"]            # 云不可达（URLError）
    assert results[1]["ok"]                # 本地探活可达（FakeLlm.alive）
