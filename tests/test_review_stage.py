"""复核 Agent 测试：高风险低置信度与条款未匹配进入人工复核。"""

from pipeline.base import StageMessage
from pipeline.review import ReviewStage


def _msg(payload: dict) -> StageMessage:
    return StageMessage(
        task_id="t_review", agent="review", status="pending",
        payload=payload, error=None, cost_ms=0)


def test_low_conf_critical_requires_review():
    out = ReviewStage().run(_msg({
        "risk_level": "重大",
        "detections": [{"cls": "spark", "conf": 0.42}],
        "compliance": [],
    }))
    assert out.status == "success"
    assert out.payload["needs_review"] is True
    assert out.payload["review_reasons"]


def test_high_conf_critical_no_review():
    out = ReviewStage().run(_msg({
        "risk_level": "重大",
        "detections": [{"cls": "spark", "conf": 0.93}],
        "compliance": [{"needs_review": False}],
    }))
    assert out.payload["needs_review"] is False


def test_rule_missing_clause_requires_review():
    out = ReviewStage().run(_msg({
        "risk_level": "较大",
        "detections": [],
        "compliance": [{"label": "火花", "needs_review": True}],
    }))
    assert out.payload["needs_review"] is True


def test_low_conf_high_risk_at_low_risk_level_requires_review():
    """盲区回归：risk_level=低 时高风险低置信度也必须复核。

    修复前 review_agent 仅在 risk_level 为较大/重大时才查高风险低置信度，
    导致 PPE 类（no_helmet/no_vest）不在 hot_work 矩阵、风险升不上去时
    复核被完全跳过，UI 显示"无需人工复核"。
    """
    out = ReviewStage().run(_msg({
        "risk_level": "低",
        "detections": [{"cls": "no_helmet", "conf": 0.30}],
        "compliance": [],
    }))
    assert out.payload["needs_review"] is True
    assert out.payload["review_reasons"]


def test_permit_noncompliant_at_low_risk_requires_review():
    """盲区回归：risk_level=低 时作业票不合规也须复核。"""
    out = ReviewStage().run(_msg({
        "risk_level": "低",
        "detections": [],
        "compliance": [{"verdict": "不合规", "label": "监火人"}],
    }))
    assert out.payload["needs_review"] is True


# ---------- 低置信度 LLM 辅助理解（异步落证据链） ----------

import json
import time

import pytest

import dao.db as dao_db
from dao.db import get_conn, init_db
from dao.models import AgentRunDAO, TaskDAO, UserDAO


@pytest.fixture
def assist_env(tmp_path, monkeypatch):
    monkeypatch.setattr(dao_db, "DEFAULT_DB_PATH", str(tmp_path / "assist.db"))
    conn = get_conn()
    init_db(conn)
    admin = UserDAO(conn).insert("admin", "hashed", "admin")
    tid = TaskDAO(conn).insert(admin, "{}", "completed", source="upload")
    yield conn, tid
    conn.close()


class FakeEng:
    """可编程 ChatClient 桩：返回固定建议/抛异常/无可用 provider。"""
    outcome = "ok"
    text = "可能原因:距离远。复核要点:核对灭火器压力表。建议:2小时内复核。"

    def available_provider(self):
        return None if FakeEng.outcome == "unavailable" else "local"

    def chat(self, system, user, **kwargs):
        from core.chat_client import ChatResult
        if FakeEng.outcome == "raise":
            raise RuntimeError("ollama down")
        if FakeEng.outcome == "empty":
            return ChatResult(content=None, provider="local",
                              status="failed", cost_ms=1, error="LLM 空输出")
        return ChatResult(content=FakeEng.text, provider="local",
                          status="degraded", cost_ms=1)


def _wait_assist(conn, tid, timeout=8.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        rows = AgentRunDAO(conn).list_by_task(tid)
        hits = [r for r in rows if r["agent"] == "llm_assist"]
        if hits:
            return hits[-1]
        time.sleep(0.1)
    return None


def test_assist_async_persists_advice(assist_env, monkeypatch):
    from pipeline.review import ReviewStage

    conn, tid = assist_env
    monkeypatch.setattr("pipeline.review.get_chat_client",
                        lambda: FakeEng())
    ReviewStage().assist_async(
        tid, [{"cls": "smoke", "conf": 0.40, "scene": "hot_work"}],
        [], "重大", ["smoke 置信度 0.40 低于 0.55，属高风险项，建议人工复核"])
    row = _wait_assist(conn, tid)
    assert row is not None and row["status"] == "success"
    out = json.loads(row["output_json"])
    assert "灭火器压力表" in out["advice"]
    assert out["review_reasons"]
    # 不改变定级:advice 是文本,风险等级仍在 risks 表由规则决定


def test_assist_async_llm_unavailable_skips(assist_env, monkeypatch):
    from pipeline.review import ReviewStage

    conn, tid = assist_env
    FakeEng.outcome = "unavailable"
    monkeypatch.setattr("pipeline.review.get_chat_client",
                        lambda: FakeEng())
    ReviewStage().assist_async(tid, [{"cls": "smoke", "conf": 0.4}], [], "重大",
                               ["低置信"])
    row = _wait_assist(conn, tid)
    assert row is not None and row["status"] == "skipped"
    FakeEng.outcome = "ok"


def test_assist_async_llm_error_lands_failed(assist_env, monkeypatch):
    from pipeline.review import ReviewStage

    conn, tid = assist_env
    FakeEng.outcome = "raise"
    monkeypatch.setattr("pipeline.review.get_chat_client",
                        lambda: FakeEng())
    ReviewStage().assist_async(tid, [{"cls": "smoke", "conf": 0.4}], [], "重大",
                               ["低置信"])
    row = _wait_assist(conn, tid)
    assert row is not None and row["status"] == "failed"
    assert "ollama down" in (row["error"] or "")
    FakeEng.outcome = "ok"
