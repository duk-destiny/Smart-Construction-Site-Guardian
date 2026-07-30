"""编排器测试（TDD：并行 + 超时降级 + 崩溃兜底）。"""

from agents.base import AgentMessage
from agents.orchestrator import Orchestrator


class _StubAgent:
    def __init__(self, result: AgentMessage, delay: float = 0.0):
        self._result = result
        self._delay = delay
    def run(self, msg: AgentMessage) -> AgentMessage:
        if self._delay:
            import time as _t
            _t.sleep(self._delay)
        return self._result


def _ok(agent, payload=None):
    return AgentMessage(task_id="t", agent=agent, status="success",
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
