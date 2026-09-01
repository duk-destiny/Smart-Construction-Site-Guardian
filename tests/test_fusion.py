"""融合 Agent 测试（TDD：风险定级 + 误报过滤 + 白名单外过滤）。"""

import pytest
from pipeline.base import StageMessage
from pipeline.fusion import FusionStage


@pytest.fixture
def fusion():
    return FusionStage()


def test_fusion_major(fusion):
    """火花 + 不合规 → 重大。"""
    msg = StageMessage(
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
    msg = StageMessage(
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
    msg = StageMessage(
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


def test_fusion_safe_ppe_signals_low(fusion):
    """检测到防护面罩 / 灭火器 → 低风险正向信号。"""
    msg = StageMessage(
        task_id="t4", agent="fusion", status="pending",
        payload={
            "detections": [{"cls": "face_shield", "conf": 0.8}],
            "compliance": [],
        },
        error=None, cost_ms=0,
    )
    out = fusion.run(msg)
    assert out.payload["risk_level"] == "低"

    msg = StageMessage(
        task_id="t4b", agent="fusion", status="pending",
        payload={
            "detections": [{"cls": "extinguisher", "conf": 0.8}],
            "compliance": [],
        },
        error=None, cost_ms=0,
    )
    out = fusion.run(msg)
    assert out.payload["risk_level"] == "低"


def test_whitelist_filtered(fusion):
    """白名单外目标不纳入风险定级。"""
    msg = StageMessage(
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
    msg = StageMessage(
        task_id="t6", agent="fusion", status="pending",
        payload={"detections": [], "compliance": []},
        error=None, cost_ms=0,
    )
    out = fusion.run(msg)
    assert out.payload["risk_level"] == "低"


def test_ppe_contradiction_filtered(fusion):
    """helmet/no_helmet 矛盾框进入误报，不触发 PPE 风险。"""
    msg = StageMessage(
        task_id="t7", agent="fusion", status="pending",
        payload={
            "detections": [
                {"cls": "helmet", "conf": 0.72, "bbox": [0.5, 0.5, 0.3, 0.3]},
                {"cls": "no_helmet", "conf": 0.55, "bbox": [0.5, 0.5, 0.4, 0.4]},
            ],
            "compliance": [],
        },
        error=None, cost_ms=0,
    )
    out = fusion.run(msg)
    assert any(fp["cls"] == "no_helmet" for fp in out.payload["filtered_fp"])
    assert out.payload["risk_level"] == "低"
