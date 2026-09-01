"""Plan-and-Execute 内核测试（设计文档 §9.2/§9.3）：Fake ChatClient 注入，
不打网络、不起真实工具。

覆盖：规划失败重试与预算耗尽收敛（degraded）、截断不补全（落失败态）、
白名单拒绝、步骤失败隔离、本地档 ≤4 步收紧、side_effect 挂起-恢复、
build_digest ≤300 字。
"""
from __future__ import annotations

import json
import time

import pytest
from pydantic import BaseModel

from core.chat_client import ChatResult
from dao.db import get_conn, init_db
from dao.models import AgentChatDAO, UserDAO
from services.agent.kernel import PlanExecutor, build_digest, validate_plan_obj
from services.agent.models import Plan, RunContext, StepResult
from services.agent.tools import ToolSpec


# ---------- Fake ChatClient（脚本化应答，记录调用）----------

class FakeChatClient:
    def __init__(self, replies: list):
        self.replies = list(replies)
        self.calls: list[dict] = []

    def chat(self, system, user, *, json_schema=None, max_tokens=1024,
             total_deadline_sec=30.0, provider=None) -> ChatResult:
        self.calls.append({"json_schema": json_schema, "user": user})
        if not self.replies:
            return ChatResult(content=None, status="failed",
                              error="脚本应答耗尽")
        r = self.replies.pop(0)
        if callable(r):
            r = r()
        return r


class NoArgs(BaseModel):
    pass


def _ok_plan(steps: list[dict], goal: str = "测试目标") -> dict:
    return {"goal": goal, "steps": steps}


def _mk_registry(pay_calls: dict | None = None) -> dict[str, ToolSpec]:
    """两个无副作用工具（echo/boom）+ 一个副作用工具（pay）。"""

    def _echo(args, ctx):
        return {"status": "success", "data": {"echo": ctx.user_id}}

    def _boom(args, ctx):
        raise RuntimeError("工具内部爆炸")

    def _pay(args, ctx):
        if pay_calls is not None:
            pay_calls["n"] += 1
        return {"status": "success", "data": {"queued": True}}

    return {
        "echo": ToolSpec(fn=_echo, desc="回显", args_schema=NoArgs),
        "boom": ToolSpec(fn=_boom, desc="必炸", args_schema=NoArgs),
        "pay": ToolSpec(fn=_pay, desc="副作用", args_schema=NoArgs,
                        side_effect=True),
    }


@pytest.fixture()
def env():
    conn = get_conn(":memory:")
    init_db(conn)
    uid = UserDAO(conn).insert("alice", "hash", "safety")
    dao = AgentChatDAO(conn)
    sid = dao.create_session(uid)
    rid = dao.create_run(sid, uid, "帮我看看")
    assert dao.transition_status(rid, "pending", "running")
    return {"conn": conn, "dao": dao, "uid": uid, "sid": sid, "rid": rid}


def _ctx(env, deadline_sec: float = 30.0) -> RunContext:
    return RunContext(run_id=env["rid"], session_id=env["sid"],
                      user_id=env["uid"], role="safety",
                      user_input="帮我看看", deadline_sec=deadline_sec)


def _executor(env, client, registry, **kw) -> PlanExecutor:
    return PlanExecutor(env["dao"], client, registry, **kw)


# ---------- 规划：重试 / 截断不补全 / 白名单 ----------

def test_plan_retry_once_then_success(env):
    """非法计划重试 ≤1 次后成功（重试计入调用次数）。"""
    client = FakeChatClient([
        ChatResult(content="不是 JSON 的截断输出", status="success"),
        ChatResult(content=_ok_plan([{"tool": "echo", "args": {},
                                       "reason": "试一下"}]),
                   status="success"),
        ChatResult(content="汇总答案", status="success"),
    ])
    out = _executor(env, client, _mk_registry()).run(_ctx(env))
    assert out.status == "completed"
    assert out.answer == "汇总答案"
    # 2 次规划（1 次重试）+ 1 次汇总 = 3
    assert len(client.calls) == 3


def test_plan_invalid_twice_fails_without_completion(env):
    """两次规划均非法 → 直接落失败态，绝不让 LLM 续写（截断不补全）。"""
    client = FakeChatClient([
        ChatResult(content={"goal": "x", "steps": "非法类型"},
                   status="success"),
        ChatResult(content="{broken json", status="success"),
    ])
    out = _executor(env, client, _mk_registry()).run(_ctx(env))
    assert out.status == "failed"
    assert "规划失败" in (out.error or "")
    assert len(client.calls) == 2          # 无第三次，也无汇总调用
    assert env["dao"].get_run(env["rid"])["status"] == "running"  # 终态由服务层落


def test_plan_unknown_tool_rejected(env):
    """白名单外的工具名（prompt 注入样例）两次均被拒 → failed。"""
    evil = _ok_plan([{"tool": "drop_tables", "args": {}, "reason": "注入"}])
    client = FakeChatClient([
        ChatResult(content=evil, status="success"),
        ChatResult(content=evil, status="success"),
    ])
    out = _executor(env, client, _mk_registry()).run(_ctx(env))
    assert out.status == "failed"
    assert "白名单" in (out.error or "")


def test_budget_exhausted_converges_degraded(env):
    """总预算耗尽 → 强制收敛，整体 degraded（非 failed）。"""

    def _slow_fail():
        time.sleep(0.3)
        return ChatResult(content=None, status="failed", error="慢且失败")

    client = FakeChatClient([_slow_fail])
    out = _executor(env, client, _mk_registry()).run(
        _ctx(env, deadline_sec=0.2))
    assert out.status == "degraded"
    assert "预算" in (out.error or "")


# ---------- 执行：失败隔离 / 本地档收紧 / 步骤上限 ----------

def test_step_failure_isolated(env):
    """单工具 failed 不拖垮整 run，后续步骤照常执行。"""
    client = FakeChatClient([
        ChatResult(content=_ok_plan([
            {"tool": "boom", "args": {}, "reason": "先炸"},
            {"tool": "echo", "args": {}, "reason": "再试"}]),
            status="success"),
        ChatResult(content="部分成功", status="success"),
    ])
    out = _executor(env, client, _mk_registry()).run(_ctx(env))
    steps = env["dao"].list_steps(env["rid"])
    assert [s["tool"] for s in steps] == ["boom", "echo"]
    assert steps[0]["status"] == "failed" and steps[0]["error"]
    assert steps[1]["status"] == "success"
    assert out.status == "degraded"        # 有失败步骤 → 整体降级但完成
    assert out.answer == "部分成功"


def test_degraded_provider_tightens_to_four_steps(env):
    """本地档（degraded）计划收紧 ≤4 步：6 步计划只执行前 4 步。"""
    six = [{"tool": "echo", "args": {}, "reason": f"步{i}"} for i in range(6)]
    client = FakeChatClient([
        ChatResult(content=_ok_plan(six), status="degraded"),
        ChatResult(content="本地档汇总", status="degraded"),
    ])
    out = _executor(env, client, _mk_registry()).run(_ctx(env))
    steps = env["dao"].list_steps(env["rid"])
    assert len(steps) == 4
    assert out.status == "degraded"
    plan = json.loads(env["dao"].get_run(env["rid"])["plan_json"])
    assert len(plan["steps"]) == 4


def test_validate_plan_rejects_over_limit():
    """超 8 步计划被 pydantic 硬上限拒绝。"""
    nine = [{"tool": "echo", "args": {}, "reason": ""} for _ in range(9)]
    with pytest.raises(Exception):
        validate_plan_obj(_ok_plan(nine), _mk_registry())


# ---------- 副作用挂起-恢复 ----------

def test_side_effect_suspends_then_resumes_once(env):
    """遇副作用步骤挂起；恢复后仅执行一次该步骤，不再重复挂起。"""
    pay_calls = {"n": 0}
    registry = _mk_registry(pay_calls)
    plan_obj = _ok_plan([
        {"tool": "echo", "args": {}, "reason": "先查"},
        {"tool": "pay", "args": {}, "reason": "再建草稿"}])
    client = FakeChatClient([
        ChatResult(content=plan_obj, status="success"),
        ChatResult(content="确认后汇总", status="success"),
    ])
    ex = _executor(env, client, registry)
    out = ex.run(_ctx(env))
    assert out.status == "pending_confirm"
    row = env["dao"].get_run(env["rid"])
    assert row["status"] == "pending_confirm"
    assert row["need_confirm"] == 1
    payload = json.loads(row["confirm_payload"])
    assert payload["tool"] == "pay" and payload["step_idx"] == 1
    assert pay_calls["n"] == 0             # 挂起时副作用零执行

    # 恢复（模拟 confirm 原子翻转后）
    assert env["dao"].transition_status(env["rid"], "pending_confirm",
                                        "running")
    ctx2 = RunContext(run_id=env["rid"], session_id=env["sid"],
                      user_id=env["uid"], role="safety",
                      user_input="帮我看看",
                      plan=Plan.model_validate(plan_obj),
                      status="running", current_step_idx=0,
                      deadline_sec=30.0,
                      steps=[StepResult(step_idx=0, tool="echo", status="success")])
    out2 = _executor(env, client, registry).resume(ctx2)
    assert out2.status == "completed"
    assert pay_calls["n"] == 1             # 幂等：只执行一次
    assert env["dao"].get_step(env["rid"], 1)["status"] == "success"


def test_resume_with_no_plan_fails(env):
    out = _executor(env, FakeChatClient([]), _mk_registry()).resume(
        _ctx(env))
    assert out.status == "failed"


# ---------- digest ----------

def test_build_digest_le_300_chars():
    """摘要由代码拼接且 ≤300 字（§5.7，零 LLM）。"""
    steps = [StepResult(step_idx=i, tool="echo", status="success",
                        digest="超长摘要" * 40) for i in range(20)]
    ctx = RunContext(run_id="r", session_id="s", user_id="u",
                     user_input="很长的用户输入" * 30,
                     plan=Plan(goal="长目标" * 30,
                               steps=[{"tool": "echo", "args": {},
                                       "reason": ""}] * 1),
                     steps=steps)
    d = build_digest(ctx)
    assert len(d) <= 300
    assert d.startswith("目标:")


def test_finish_skips_synthesis_when_budget_short(env):
    """剩余预算不足 5s → 跳过汇总、以 digest 作答并记 degraded。"""
    client = FakeChatClient([
        ChatResult(content=_ok_plan([{"tool": "echo", "args": {},
                                       "reason": "快"}]),
                   status="success"),
    ])
    ex = _executor(env, client, _mk_registry())
    # 汇总预留调大到超过剩余预算 → 必跳过汇总
    ex._reserve = 10_000.0
    out = ex.run(_ctx(env))
    assert out.status == "degraded"
    assert len(client.calls) == 1          # 仅规划一次，无汇总调用
    assert out.answer and "echo" in out.answer
