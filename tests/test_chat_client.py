"""统一 LLM 入口 ChatClient 测试（v2.1 §5.1/§6 降级矩阵）。

不触网：云端档 monkeypatch `ChatClient._make_client` 注入假 openai 客户端，
本地档 monkeypatch `core.llm_engine.LlmEngine`。
覆盖：云端成功 / 云端失败降级本地成功 / 全失败 / 预算耗尽 / 断路器跳档，
以及显式指定档（provider=）与 json_schema 结构化输出校验。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import core.chat_client as cc
from core.chat_client import ChatClient

CLOUD = {"name": "cloud", "type": "cloud", "api_base": "https://x/v1",
         "api_key": "k", "model": "m", "timeout_sec": 20}
LOCAL = {"name": "local", "type": "local", "api_base": "",
         "api_key": "", "model": "", "timeout_sec": 20}


class FakeLlm:
    """core.llm_engine.LlmEngine 替身：chat/ask_json/探活均可控，不触网。"""

    alive = True
    text = "本地润色"
    json_out: dict | None = {"hazard_key": "flammable"}
    chats: list = []
    asks: list = []

    def __init__(self, model=None, **kw):
        self.model = model or "fake-model"

    def available(self):
        return FakeLlm.alive

    def chat(self, system, user, num_predict=None):
        FakeLlm.chats.append((system, user, num_predict))
        return FakeLlm.text if FakeLlm.alive else None

    def ask_json(self, instruction):
        FakeLlm.asks.append(instruction)
        return FakeLlm.json_out if FakeLlm.alive else None


class _Resp:
    def __init__(self, content):
        self.choices = [SimpleNamespace(
            message=SimpleNamespace(content=content))]


class FakeOpenAI:
    """假 openai 客户端：create 按预设返回文本或抛异常。"""

    def __init__(self, fail: bool = False, content: str = "云端回复"):
        self.fail = fail
        self.content = content
        self.calls: list[dict] = []
        outer = self

        class _Completions:
            def create(self, **kwargs):
                outer.calls.append(kwargs)
                if outer.fail:
                    raise RuntimeError("cloud down")
                return _Resp(outer.content)

        self.chat = SimpleNamespace(completions=_Completions())


@pytest.fixture(autouse=True)
def _reset_fake():
    FakeLlm.alive = True
    FakeLlm.text = "本地润色"
    FakeLlm.json_out = {"hazard_key": "flammable"}
    FakeLlm.chats = []
    FakeLlm.asks = []


def _client(fake_cloud: FakeOpenAI | None, monkeypatch) -> ChatClient:
    """providers 显式注入（不读真实配置）；云端客户端缝替换为假实现。"""
    client = ChatClient(providers=[dict(CLOUD), dict(LOCAL)])
    if fake_cloud is not None:
        monkeypatch.setattr(
            client, "_make_client", lambda p, timeout: fake_cloud)
    return client


# ---------- 降级矩阵组合 1：云端可用 → success（正常态） ----------

def test_cloud_success(monkeypatch):
    fake = FakeOpenAI(content='{"a": 1}')
    client = _client(fake, monkeypatch)
    monkeypatch.setattr("core.llm_engine.LlmEngine", FakeLlm)

    # 纯文本：无 json_schema 直接返回原文
    r = client.chat("sys", "user")
    assert r.status == "success" and r.provider == "cloud"
    assert r.content == '{"a": 1}' and r.error is None
    assert FakeLlm.chats == []                    # 未落本地档

    # 结构化输出：启用 JSON mode，返回后过二次校验 → dict
    r2 = client.chat("sys", "user", json_schema={"type": "object"})
    assert r2.status == "success" and r2.content == {"a": 1}
    assert fake.calls[-1]["response_format"] == {"type": "json_object"}
    # SDK 侧 max_retries=0 由 _make_client 构造参数约束（不内置重试）


# ---------- 降级矩阵组合 2：云端失败 → 本地成功（degraded） ----------

def test_cloud_fail_falls_to_local(monkeypatch):
    fake = FakeOpenAI(fail=True)
    client = _client(fake, monkeypatch)
    monkeypatch.setattr("core.llm_engine.LlmEngine", FakeLlm)

    r = client.chat("sys", "user")
    assert r.status == "degraded" and r.provider == "local"
    assert r.content == "本地润色" and r.error is None
    assert len(fake.calls) == 1 and FakeLlm.chats  # 云端试过、本地兜住


# ---------- 降级矩阵组合 3：云端、本地全失败 → failed ----------

def test_all_providers_fail(monkeypatch):
    fake = FakeOpenAI(fail=True)
    client = _client(fake, monkeypatch)
    FakeLlm.alive = False                          # 本地 chat 返回 None
    monkeypatch.setattr("core.llm_engine.LlmEngine", FakeLlm)

    r = client.chat("sys", "user")
    assert r.status == "failed" and r.content is None
    assert r.provider == "local"                   # 最后尝试档
    assert r.error                                   # 失败留因（调用方模板兜底）


# ---------- 降级矩阵组合 4：总预算耗尽 → 立即 failed，不发任何调用 ----------

def test_budget_exhausted_stops_chain(monkeypatch):
    fake = FakeOpenAI()
    client = _client(fake, monkeypatch)
    monkeypatch.setattr("core.llm_engine.LlmEngine", FakeLlm)

    r = client.chat("sys", "user", total_deadline_sec=-1.0)
    assert r.status == "failed" and r.content is None
    assert "总预算" in (r.error or "")
    assert fake.calls == [] and FakeLlm.chats == []   # 双档均未发起


# ---------- 降级矩阵组合 5：断路器 —— 云端连续失败后跳档，窗口后恢复探测 ----------

def test_circuit_breaker_skips_cloud_then_recovers(monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr(cc.time, "monotonic", lambda: clock["now"])
    fake = FakeOpenAI(fail=True)
    client = _client(fake, monkeypatch)
    monkeypatch.setattr("core.llm_engine.LlmEngine", FakeLlm)

    # 连续 3 次云端失败（每次都降级本地成功）→ 达到阈值，断路器开启
    for _ in range(3):
        r = client.chat("sys", "user")
        assert r.status == "degraded" and r.provider == "local"
    assert len(fake.calls) == 3

    # 窗口内第 4 次：云端被直接跳过（不再发起云端调用）
    r = client.chat("sys", "user")
    assert r.status == "degraded" and r.provider == "local"
    assert len(fake.calls) == 3

    # 30s 窗口过后：恢复探测云端（只加速降级、不影响恢复）
    clock["now"] += cc._CLOUD_BREAKER_SEC + 1
    client.chat("sys", "user")
    assert len(fake.calls) == 4


# ---------- 显式指定档（lab_service 按 base 对比用） ----------

def test_pinned_provider_no_fallback(monkeypatch):
    fake = FakeOpenAI()
    client = _client(fake, monkeypatch)
    monkeypatch.setattr("core.llm_engine.LlmEngine", FakeLlm)

    r = client.chat("sys", "user", provider="local")
    assert r.status == "degraded" and r.provider == "local"
    assert fake.calls == []                        # 指定档不触云端

    r2 = client.chat("sys", "user", provider="ghost")
    assert r2.status == "failed" and "未知 provider" in (r2.error or "")


def test_pinned_provider_ignores_breaker(monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr(cc.time, "monotonic", lambda: clock["now"])
    fake = FakeOpenAI(fail=True)
    client = _client(fake, monkeypatch)
    monkeypatch.setattr("core.llm_engine.LlmEngine", FakeLlm)

    for _ in range(cc._CLOUD_FAIL_LIMIT):          # 触发断路器
        client.chat("sys", "user")
    client.chat("sys", "user", provider="cloud")   # 显式指定档照常试探
    assert len(fake.calls) == cc._CLOUD_FAIL_LIMIT + 1


# ---------- json_schema：本地档经 ask_json + schema 校验 ----------

def test_json_schema_local_validation(monkeypatch):
    client = ChatClient(providers=[dict(LOCAL)])
    monkeypatch.setattr("core.llm_engine.LlmEngine", FakeLlm)

    schema = {"type": "object", "properties": {
        "hazard_key": {"enum": ["flammable", "spark"]}}}
    r = client.chat("sys", "user", json_schema=schema)
    assert r.status == "degraded" and r.content == {"hazard_key": "flammable"}

    # 越 schema 输出 → 校验失败 → failed（不猜测、不降级补全）
    FakeLlm.json_out = {"hazard_key": "ufo"}
    r2 = client.chat("sys", "user", json_schema=schema)
    assert r2.status == "failed" and r2.content is None


def test_no_provider_configured(monkeypatch):
    client = ChatClient(providers=[])
    r = client.chat("sys", "user")
    assert r.status == "failed" and r.provider == "none"
    assert "未配置" in (r.error or "")
