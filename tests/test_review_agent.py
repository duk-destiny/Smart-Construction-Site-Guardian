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
