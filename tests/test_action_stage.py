"""闭环处置 Agent 测试（TDD：工单组装 + LLM 润色/模板降级，注入假 LLM）。"""

from pipeline.action import ActionStage
from pipeline.base import StageMessage


class _FakeLlm:
    """可控假 LLM：polish 返回预设文本或 None。"""
    def __init__(self, returns=None):
        self._returns = returns
    def available(self):
        return self._returns is not None
    def polish(self, prompt):
        return self._returns


def test_action_template_fallback():
    """LLM 不可用（返回 None）→ 模板降级必有 worker_notice。"""
    msg = StageMessage(
        task_id="t1", agent="action", status="pending",
        payload={
            "risk_level": "重大",
            "reasons": ["明火无监护"],
            "compliance": [{"label": "火花", "verdict": "不合规", "clause_ref": "一"}],
            "training_tips": ["作业前必须指定专职监火人"],
        },
        error=None, cost_ms=0,
    )
    out = ActionStage(llm=_FakeLlm(None)).run(msg)
    assert out.status == "success"
    wo = out.payload["work_order"]
    assert wo["risk_level"] == "重大"
    assert out.payload["worker_notice"], "模板降级 worker_notice 不应为空"
    assert "隐患说明" in out.payload["worker_notice"]


class _FakeWoDao:
    """捕获 update_notice 调用，供异步润色验证。"""
    def __init__(self):
        import threading as _t
        self._event = _t.Event()
        self.captured = None
    def update_notice(self, task_id, worker_notice):
        self.captured = (task_id, worker_notice)
        self._event.set()


def test_action_llm_polish_used():
    """工单落库后调用 polish() → 异步润色并通过 work_order_dao.update_notice 回填。

    主链路 run() 只返回模板（快速、可计时）；润色在 polish() 后台线程完成，
    不计入主链路耗时（LLD §5.1）。
    """
    import time as _t
    wo_dao = _FakeWoDao()
    msg = StageMessage(
        task_id="t2", agent="action", status="pending",
        payload={
            "risk_level": "较大", "reasons": ["未戴面罩"],
            "compliance": [], "training_tips": [],
        },
        error=None, cost_ms=0,
    )
    agent = ActionStage(llm=_FakeLlm("兄弟，动火记得戴面罩！"), work_order_dao=wo_dao)
    out = agent.run(msg)
    assert out.status == "success"
    # 主链路返回的是模板（快速、可计时）
    assert "隐患说明" in out.payload["worker_notice"]
    # 工单落库后由调用方触发润色（模拟 save_result 之后的回填）
    wo = out.payload["work_order"]
    agent.polish("t2", wo["hazard_desc"], wo["clause"],
                 wo["requirement"], wo["deadline"])
    # 后台润色线程完成后回填
    assert wo_dao._event.wait(timeout=5.0), "异步润色未触发 update_notice"
    assert wo_dao.captured[0] == "t2"
    assert wo_dao.captured[1] == "兄弟，动火记得戴面罩！"
    _t.sleep(0.01)


def test_action_low_risk_complete():
    """一般风险：工单字段完整。"""
    msg = StageMessage(
        task_id="t3", agent="action", status="pending",
        payload={"risk_level": "一般", "reasons": ["周边易燃物未清理"],
                 "compliance": [], "training_tips": []},
        error=None, cost_ms=0,
    )
    out = ActionStage(llm=_FakeLlm(None)).run(msg)
    assert out.status == "success"
    assert out.payload["work_order"]["risk_level"] == "一般"
