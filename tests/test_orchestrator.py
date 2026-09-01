"""编排器测试（TDD：并行 + 超时降级 + 崩溃兜底）。"""

import pytest

from pipeline.base import StageMessage
from pipeline.orchestrator import Orchestrator


class _StubAgent:
    def __init__(self, result: StageMessage, delay: float = 0.0):
        self._result = result
        self._delay = delay
    def run(self, msg: StageMessage) -> StageMessage:
        if self._delay:
            import time as _t
            _t.sleep(self._delay)
        return self._result


def _ok(agent, payload=None):
    return StageMessage(task_id="t", agent=agent, status="success",
                        payload=payload or {}, error=None, cost_ms=10)


def test_orchestrator_parallel_success():
    """正常链路：4 Agent 全成功，整体 success。"""
    orch = Orchestrator(
        vision=_StubAgent(_ok("vision", {"detections": [{"cls": "spark", "conf": 0.9}], "violation_descs": ["spark"]})),
        rule=_StubAgent(_ok("rule", {"compliance": [{"verdict": "不合规"}], "training_tips": ["a"]})),
        fusion=_StubAgent(_ok("fusion", {"risk_level": "重大", "reasons": ["x"]})),
        action=_StubAgent(_ok("action", {"work_order": {"risk_level": "重大"}, "worker_notice": "n"})),
    )
    out = orch.execute("t1", images=["x.jpg"], permit_info={})
    assert out.status == "success"
    assert out.payload["risk_level"] == "重大"
    assert out.payload["work_order"]["risk_level"] == "重大"


def test_orchestrator_parallel_timeout():
    """视觉超时 → 整体降级，不崩。"""
    orch = Orchestrator(
        vision=_StubAgent(_ok("vision"), delay=4.0),  # 超过 3s 超时
        rule=_StubAgent(_ok("rule", {"compliance": [], "training_tips": []})),
        fusion=_StubAgent(_ok("fusion", {"risk_level": "一般", "reasons": []})),
        action=_StubAgent(_ok("action", {"work_order": {"risk_level": "一般"}, "worker_notice": "n"})),
    )
    out = orch.execute("t2", images=["x.jpg"], permit_info={})
    assert out.status in ("success", "degraded")
    # 视觉节点应被标记为 degraded
    assert out.payload["vision"].get("status") == "degraded"


def test_orchestrator_crash_isolated():
    """某 Agent 抛异常 → 标红但不退出，整体可降级。"""
    class _Boom:
        def run(self, msg):
            raise RuntimeError("boom")
    orch = Orchestrator(
        vision=_Boom(),
        rule=_StubAgent(_ok("rule", {"compliance": [], "training_tips": []})),
        fusion=_StubAgent(_ok("fusion", {"risk_level": "一般", "reasons": []})),
        action=_StubAgent(_ok("action", {"work_order": {"risk_level": "一般"}, "worker_notice": "n"})),
    )
    out = orch.execute("t3", images=["x.jpg"], permit_info={})
    # 顶层不应抛出；视觉节点 failed
    assert out.payload["vision"].get("status") == "failed"


class _RecordingRule:
    """记录每次被调用的入参消息（验证一/二阶段行为）。"""

    def __init__(self):
        self.messages = []

    def run(self, msg):
        self.messages.append(msg)
        return StageMessage(
            task_id=msg.task_id, agent="rule", status="success",
            payload={
                "compliance": [{
                    "field": "watcher", "label": "监火人",
                    "verdict": "不合规", "clause_ref": "",
                }],
                "training_tips": [],
            }, error=None, cost_ms=5)


def _orch_with_recording_rule():
    recording = _RecordingRule()
    orch = Orchestrator(
        vision=_StubAgent(_ok("vision", {
            "detections": [{"cls": "spark", "conf": 0.9}],
            "violation_descs": ["火花"],
        })),
        rule=recording,
        fusion=_StubAgent(_ok("fusion", {"risk_level": "重大", "reasons": ["x"]})),
        action=_StubAgent(_ok("action", {
            "work_order": {"risk_level": "重大"}, "worker_notice": "n",
        })),
    )
    return orch, recording


def test_orchestrator_rule_preflight_then_refine():
    """规范 Agent 先做免 RAG 预检，拿到视觉证据后再补全条款检索。"""
    orch, recording = _orch_with_recording_rule()
    out = orch.execute("t4", images=["x.jpg"], permit_info={"watcher": ""})
    assert out.status == "success"
    assert len(recording.messages) == 2
    assert recording.messages[0].payload.get("skip_rag") is True
    assert recording.messages[1].payload.get("skip_rag") is False
    assert recording.messages[1].payload.get("violation_descs") == ["火花"]


def test_orchestrator_quick_mode_skips_second_stage():
    """mode="quick"：跳过二阶段 RAG 回灌，规范 Agent 仅被调一次（预检）。"""
    orch, recording = _orch_with_recording_rule()
    out = orch.execute("t5", images=["x.jpg"], permit_info={"watcher": ""},
                       mode="quick")
    assert out.status == "success"
    assert len(recording.messages) == 1
    assert recording.messages[0].payload.get("skip_rag") is True
    # 其余阶段不受影响：融合/闭环结论仍在
    assert out.payload["risk_level"] == "重大"
    assert out.payload["work_order"]["risk_level"] == "重大"
    assert recording.messages[0].payload.get("violation_descs") == []


def test_orchestrator_full_mode_explicit_same_as_default():
    """显式 mode="full" 与不传（默认）行为一致：两阶段都执行。"""
    orch, recording = _orch_with_recording_rule()
    out = orch.execute("t6", images=["x.jpg"], permit_info={"watcher": ""},
                       mode="full")
    assert out.status == "success"
    assert len(recording.messages) == 2
    assert recording.messages[1].payload.get("skip_rag") is False


def test_orchestrator_invalid_mode_raises():
    """非法 mode 越界即拒：抛 ValueError，不静默降级。"""
    orch, _ = _orch_with_recording_rule()
    with pytest.raises(ValueError):
        orch.execute("t7", images=["x.jpg"], permit_info={}, mode="turbo")
