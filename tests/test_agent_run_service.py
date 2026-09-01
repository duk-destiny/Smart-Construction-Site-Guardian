"""PlanRunService 测试（设计文档 §9.3）：文件库 + Fake 注入，仿
tests/test_async_run.py 范式（库落盘临时文件，worker 的 scoped() 与
测试断言连接指向同一物理库）。

覆盖：挂起-恢复幂等（重复 confirm 只续跑一次）、背压 busy、
孤儿扫描（过期 updated_at）、执行中取消。
"""
from __future__ import annotations

import json
import time

import pytest
from pydantic import BaseModel

from core.chat_client import ChatResult
from dao.db import get_conn, init_db
from dao.models import AgentChatDAO, UserDAO
from services.agent.run_service import PlanRunBusy, PlanRunService
from services.agent.tools import ToolSpec


class NoArgs(BaseModel):
    pass


def _plan(steps: list[dict]) -> dict:
    return {"goal": "测试", "steps": steps}


class FakeChatClient:
    """规划返脚本计划；汇总返固定文案。"""

    def __init__(self, plan_obj: dict):
        self._plan = plan_obj

    def chat(self, system, user, *, json_schema=None, max_tokens=1024,
             total_deadline_sec=30.0, provider=None) -> ChatResult:
        if json_schema is not None:
            return ChatResult(content=self._plan, status="success")
        return ChatResult(content="任务已完成", status="success")


@pytest.fixture()
def env(monkeypatch, tmp_path):
    import dao.db as dao_db

    db_file = str(tmp_path / "agent_run.db")
    monkeypatch.setattr(dao_db, "DEFAULT_DB_PATH", db_file)
    conn = get_conn(db_file)
    init_db(conn)
    uid = UserDAO(conn).insert("alice", "hash", "safety")
    sid = AgentChatDAO(conn).create_session(uid, title="测试会话")

    pay_calls = {"n": 0}

    def _echo(args, ctx):
        return {"status": "success", "data": {"echo": True}}

    def _pay(args, ctx):
        pay_calls["n"] += 1
        return {"status": "success", "data": {"queued": True}}

    def _slow(args, ctx):
        time.sleep(1.2)
        return {"status": "success", "data": {"slow": True}}

    registry = {
        "echo": ToolSpec(fn=_echo, desc="回显", args_schema=NoArgs),
        "pay": ToolSpec(fn=_pay, desc="副作用", args_schema=NoArgs,
                        side_effect=True),
        "slow": ToolSpec(fn=_slow, desc="慢工具", args_schema=NoArgs,
                         timeout_sec=5.0),
    }
    monkeypatch.setattr(PlanRunService, "_REGISTRY", registry)
    # 跨例隔离活跃登记（类级状态）
    with PlanRunService._RUN_LOCK:
        PlanRunService._active_runs.clear()

    yield {"conn": conn, "uid": uid, "sid": sid, "pay_calls": pay_calls,
           "monkeypatch": monkeypatch}

    with PlanRunService._RUN_LOCK:
        PlanRunService._active_runs.clear()


def _inject_client(env, plan_obj):
    fake = FakeChatClient(plan_obj)
    env["monkeypatch"].setattr(PlanRunService, "_CHAT_FACTORY",
                               lambda: fake)
    return fake


def _wait_status(conn, run_id, statuses, timeout=10.0) -> str:
    """轮询直至落入目标状态集（返回最终状态；超时返回当前状态）。"""
    dao = AgentChatDAO(conn)
    t0 = time.time()
    last = ""
    while time.time() - t0 < timeout:
        row = dao.get_run(run_id)
        last = row["status"]
        if last in statuses:
            return last
        time.sleep(0.05)
    return last


_TERMINAL = ("completed", "degraded", "failed", "cancelled")


# ---------- 挂起-恢复幂等 ----------

def test_suspend_confirm_resume_idempotent(env):
    """副作用计划挂起 → 重复 confirm 只续跑一次 → 终态。"""
    _inject_client(env, _plan([
        {"tool": "echo", "args": {}, "reason": "查"},
        {"tool": "pay", "args": {}, "reason": "建草稿"}]))
    rid = PlanRunService.create_run(env["uid"], env["sid"], "建个草稿")

    status = _wait_status(env["conn"], rid, ("pending_confirm",))
    assert status == "pending_confirm"
    assert env["pay_calls"]["n"] == 0          # 挂起前副作用零执行

    r1 = PlanRunService.confirm(rid, env["uid"], "confirm")
    assert r1["status"] == "running"
    # 重复 confirm：原子查再置失败 → 不再提交第二个续跑
    r2 = PlanRunService.confirm(rid, env["uid"], "confirm")
    assert r2["status"] in ("running", *_TERMINAL)

    final = _wait_status(env["conn"], rid, _TERMINAL)
    assert final == "completed"
    assert env["pay_calls"]["n"] == 1          # 幂等：副作用仅执行一次
    # 助手消息落库（只存摘要）
    msgs = AgentChatDAO(env["conn"]).list_messages(env["sid"])
    assert any(m["role"] == "assistant" and m["run_id"] == rid for m in msgs)


def test_confirm_cancel_from_pending(env):
    """挂起态直接取消：副作用零执行。"""
    _inject_client(env, _plan([{"tool": "pay", "args": {}, "reason": "建"}]))
    rid = PlanRunService.create_run(env["uid"], env["sid"], "建草稿")
    assert _wait_status(env["conn"], rid, ("pending_confirm",)) == \
        "pending_confirm"
    res = PlanRunService.confirm(rid, env["uid"], "cancel")
    assert res["status"] == "cancelled"
    assert env["pay_calls"]["n"] == 0


def test_confirm_wrong_owner_returns_none(env):
    _inject_client(env, _plan([{"tool": "pay", "args": {}, "reason": ""}]))
    rid = PlanRunService.create_run(env["uid"], env["sid"], "建草稿")
    assert PlanRunService.confirm(rid, "u_someone_else", "confirm") is None


# ---------- 背压 ----------

def test_busy_backpressure(env, monkeypatch):
    """活跃 run 超限 → 同步返 busy（PlanRunBusy），放行后恢复受理。"""
    monkeypatch.setattr(PlanRunService, "_MAX_ACTIVE", 1)
    _inject_client(env, _plan([{"tool": "slow", "args": {}, "reason": ""}]))
    rid1 = PlanRunService.create_run(env["uid"], env["sid"], "慢任务")
    with pytest.raises(PlanRunBusy):
        PlanRunService.create_run(env["uid"], env["sid"], "再来一个")
    _wait_status(env["conn"], rid1, _TERMINAL)
    # 活跃摘除后恢复受理
    rid2 = PlanRunService.create_run(env["uid"], env["sid"], "好了吗")
    assert _wait_status(env["conn"], rid2, _TERMINAL) in _TERMINAL


# ---------- 孤儿扫描 ----------

def test_scan_orphans_marks_stale_failed(env):
    """pending/running 且 updated_at 超阈 → failed + 审计；不自动重跑。"""
    dao = AgentChatDAO(env["conn"])
    rid_run = dao.create_run(env["sid"], env["uid"], "孤儿running")
    dao.transition_status(rid_run, "pending", "running")
    rid_pen = dao.create_run(env["sid"], env["uid"], "孤儿pending")
    # 构造过期 updated_at（模拟进程重启遗留）
    env["conn"].execute(
        "UPDATE agent_chat_runs SET updated_at=datetime('now','-5 minutes') "
        "WHERE id IN (?,?)", (rid_run, rid_pen))
    env["conn"].commit()

    n = PlanRunService.scan_orphans()
    assert n == 2
    assert dao.get_run(rid_run)["status"] == "failed"
    assert dao.get_run(rid_pen)["status"] == "failed"
    assert "进程重启" in dao.get_run(rid_run)["error"]
    audits = env["conn"].execute(
        "SELECT COUNT(*) AS n FROM audit_logs "
        "WHERE action='agent_orphan_failed'").fetchone()["n"]
    assert audits == 2


def test_scan_orphans_keeps_fresh_and_pending_confirm(env):
    """新近 run 与 pending_confirm（可恢复）不被孤儿扫描误伤。"""
    dao = AgentChatDAO(env["conn"])
    rid_fresh = dao.create_run(env["sid"], env["uid"], "刚建的")
    dao.transition_status(rid_fresh, "pending", "running")
    rid_pc = dao.create_run(env["sid"], env["uid"], "等确认")
    dao.transition_status(rid_pc, "pending", "running")
    dao.transition_status(rid_pc, "running", "pending_confirm")
    env["conn"].execute(
        "UPDATE agent_chat_runs SET updated_at=datetime('now','-5 minutes') "
        "WHERE id=?", (rid_pc,))
    env["conn"].commit()

    assert PlanRunService.scan_orphans() == 0
    assert dao.get_run(rid_fresh)["status"] == "running"
    assert dao.get_run(rid_pc)["status"] == "pending_confirm"


def test_lazy_expire_pending_confirm_after_24h(env):
    """惰性 24h 取消：读路径发现超期挂起即置 cancelled。"""
    dao = AgentChatDAO(env["conn"])
    rid = dao.create_run(env["sid"], env["uid"], "等确认")
    dao.transition_status(rid, "pending", "running")
    dao.transition_status(rid, "running", "pending_confirm")
    env["conn"].execute(
        "UPDATE agent_chat_runs SET updated_at=datetime('now','-25 hours') "
        "WHERE id=?", (rid,))
    env["conn"].commit()

    view = PlanRunService.progress(rid, env["uid"])
    assert view["status"] == "cancelled"
    assert dao.get_run(rid)["status"] == "cancelled"


# ---------- 取消 ----------

def test_cancel_running(env):
    """执行中取消：条件翻转到 cancelled，worker 终态不得覆盖。"""
    _inject_client(env, _plan([{"tool": "slow", "args": {}, "reason": ""}]))
    rid = PlanRunService.create_run(env["uid"], env["sid"], "慢任务")
    assert _wait_status(env["conn"], rid, ("running",)) == "running"
    res = PlanRunService.cancel(rid, env["uid"])
    assert res == {"ok": True, "status": "cancelled"}
    # worker 收尾竞争失败，终态保持 cancelled
    time.sleep(1.5)
    assert AgentChatDAO(env["conn"]).get_run(rid)["status"] == "cancelled"


def test_progress_and_trace_owner_isolation(env):
    """跨属主读路径返 None（端点转 404）。"""
    _inject_client(env, _plan([{"tool": "echo", "args": {}, "reason": ""}]))
    rid = PlanRunService.create_run(env["uid"], env["sid"], "查一下")
    _wait_status(env["conn"], rid, _TERMINAL)
    assert PlanRunService.progress(rid, env["uid"]) is not None
    assert PlanRunService.progress(rid, "u_other") is None
    assert PlanRunService.trace(rid, "u_other") is None
    assert PlanRunService.history(env["sid"], "u_other") is None
