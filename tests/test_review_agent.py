"""复核 Agent 测试：高风险低置信度与条款未匹配进入人工复核。"""

from agents.base import AgentMessage
from agents.review_agent import ReviewAgent


def _msg(payload: dict) -> AgentMessage:
    return AgentMessage(
        task_id="t_review", agent="review", status="pending",
        payload=payload, error=None, cost_ms=0)


def test_low_conf_critical_requires_review():
    out = ReviewAgent().run(_msg({
        "risk_level": "重大",
        "detections": [{"cls": "spark", "conf": 0.42}],
        "compliance": [],
    }))
    assert out.status == "success"
    assert out.payload["needs_review"] is True
    assert out.payload["review_reasons"]


def test_high_conf_critical_no_review():
    out = ReviewAgent().run(_msg({
        "risk_level": "重大",
        "detections": [{"cls": "spark", "conf": 0.93}],
        "compliance": [{"needs_review": False}],
    }))
    assert out.payload["needs_review"] is False


def test_rule_missing_clause_requires_review():
    out = ReviewAgent().run(_msg({
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
    out = ReviewAgent().run(_msg({
        "risk_level": "低",
        "detections": [{"cls": "no_helmet", "conf": 0.30}],
        "compliance": [],
    }))
    assert out.payload["needs_review"] is True
    assert out.payload["review_reasons"]


def test_permit_noncompliant_at_low_risk_requires_review():
    """盲区回归：risk_level=低 时作业票不合规也须复核。"""
    out = ReviewAgent().run(_msg({
        "risk_level": "低",
        "detections": [],
        "compliance": [{"verdict": "不合规", "label": "监火人"}],
    }))
    assert out.payload["needs_review"] is True
