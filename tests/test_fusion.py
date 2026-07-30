"""融合 Agent 测试（TDD：风险定级 + 误报过滤 + 白名单外过滤）。"""

import pytest
from agents.base import AgentMessage
from agents.fusion_agent import FusionAgent


@pytest.fixture
def fusion():
    return FusionAgent()


def test_fusion_major(fusion):
    """火花 + 不合规 → 重大。"""
    msg = AgentMessage(
        task_id="t1", agent="fusion", status="pending",
        payload={
            "detections": [{"cls": "spark", "conf": 0.92}],
            "compliance": [{"verdict": "不合规"}],
        },
        error=None, cost_ms=0,
    )
    out = fusion.run(msg)
    assert out.status == "success"
    assert out.payload["risk_level"] == "重大", out.payload


def test_fusion_spark_compliant_low(fusion):
    """火花 + 合规 → 一般（不升级重大）。"""
    msg = AgentMessage(
        task_id="t2", agent="fusion", status="pending",
        payload={
            "detections": [{"cls": "spark", "conf": 0.9}],
            "compliance": [{"verdict": "合规"}],
        },
        error=None, cost_ms=0,
    )
    out = fusion.run(msg)
    assert out.payload["risk_level"] == "一般"


def test_fp_filter(fusion):
    """spark 低置信 → 入 filtered_fp，不升级重大。"""
    msg = AgentMessage(
        task_id="t3", agent="fusion", status="pending",
        payload={
            "detections": [{"cls": "spark", "conf": 0.30}],
            "compliance": [{"verdict": "合规"}],
        },
        error=None, cost_ms=0,
    )
    out = fusion.run(msg)
    assert out.payload["filtered_fp"], "低置信 spark 应入误报"
    assert out.payload["risk_level"] != "重大"


def test_fusion_larger_for_ppe(fusion):
    """未戴面罩 / 缺灭火器 → 较大。"""
    msg = AgentMessage(
        task_id="t4", agent="fusion", status="pending",
        payload={
            "detections": [{"cls": "face_shield", "conf": 0.8}],
            "compliance": [],
        },
        error=None, cost_ms=0,
    )
    out = fusion.run(msg)
    assert out.payload["risk_level"] == "较大"


def test_whitelist_filtered(fusion):
    """白名单外目标不纳入风险定级。"""
    msg = AgentMessage(
        task_id="t5", agent="fusion", status="pending",
        payload={
            "detections": [{"cls": "person", "conf": 0.9}],
            "compliance": [],
        },
        error=None, cost_ms=0,
    )
    out = fusion.run(msg)
    assert out.payload["risk_level"] == "低"
    assert not out.payload["reasons"][0].startswith("person")


def test_no_detection_low(fusion):
    """无检出 → 低。"""
    msg = AgentMessage(
        task_id="t6", agent="fusion", status="pending",
        payload={"detections": [], "compliance": []},
        error=None, cost_ms=0,
    )
    out = fusion.run(msg)
    assert out.payload["risk_level"] == "低"
