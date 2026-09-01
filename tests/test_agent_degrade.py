"""认知层降级与异常测试矩阵（设计文档 §9.1-§9.3，M5/T7 补全）。

与既有测试的分工（避免重复）：
- ChatClient 档位级降级 5 组合已在 tests/test_chat_client.py（mock openai/LlmEngine）；
- 内核规划异常/本地 ≤4 步/挂起-恢复基础流已在 tests/test_plan_kernel.py；
- 服务层幂等/孤儿/背压已在 tests/test_agent_run_service.py。

本文件补齐（run 级整合视角）：
- §9.1 组合 1-5 以「内核 + 剧本」整链断言：云端正常态 completed、
  云端超时落本地 degraded 且计划收紧、全败落规则模板档、模板不适用
  落 failed 但可读、非法 JSON×2 重试 ≤1 次后落模板档；
- §9.2 慢工具注入 time.sleep → 墙钟强制收敛且整体 degraded；
  本地档规划调用显式携带 max_tokens=1024（§7 num_predict 修正）；
- §9.3 模拟进程重启：孤儿扫描不误伤挂起态，pending_confirm 可恢复续跑。

零网络：全部经 Fake ChatClient 脚本化应答；文件库经 DEFAULT_DB_PATH 注入。
"""
from __future__ import annotations

import json
import time

import pytest
from pydantic import BaseModel

from core.chat_client import ChatResult
from dao.db import get_conn, init_db
from dao.models import AgentChatDAO, UserDAO
from services.agent.kernel import PlanExecutor
from services.agent.models import RunContext
from services.agent.playbooks import default_template_answer
from services.agent.run_service import PlanRunService
from services.agent.tools import ToolSpec


# ---------- Fake ChatClient（脚本化应答，记录调用）----------

class FakeChatClient:
    def __init__(self, replies: list):
        self.replies = list(replies)
        self.calls: list[dict] = []

    def chat(self, system, user, *, json_schema=None, max_tokens=1024,
             total_deadline_sec=30.0, provider=None) -> ChatResult:
        self.calls.append({"json_schema": json_schema, "user": user,
                           "max_tokens": max_tokens})
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


def _echo_step(reason: str = "查") -> dict:
    return {"tool": "echo", "args": {}, "reason": reason}


def _mk_registry(sleep_sec: float = 0.0) -> dict[str, ToolSpec]:
    def _echo(args, ctx):
        return {"status": "success", "data": {"echo": True}}

    def _slow(args, ctx):
        time.sleep(sleep_sec)
        return {"status": "success", "data": {"slow": True}}

    reg = {"echo": ToolSpec(fn=_echo, desc="回显", args_schema=NoArgs)}
    if sleep_sec:
        reg["slow"] = ToolSpec(fn=_slow, desc="慢工具", args_schema=NoArgs,
                               timeout_sec=30.0)
    return reg


@pytest.fixture()
def env(monkeypatch, tmp_path):
    """文件库（供 §9.3 服务级用例直查库断言）。"""
    import dao.db as dao_db

    db_file = str(tmp_path / "agent_degrade.db")
    monkeypatch.setattr(dao_db, "DEFAULT_DB_PATH", db_file)
    conn = get_conn(db_file)
    init_db(conn)
    uid = UserDAO(conn).insert("alice", "hash", "safety")
    dao = AgentChatDAO(conn)
    sid = dao.create_session(uid)
    yield {"conn": conn, "db_file": db_file, "dao": dao, "uid": uid,
           "sid": sid, "monkeypatch": monkeypatch}
    conn.close()


def _ctx(env, intent: str | None = None,
         deadline_sec: float = 30.0) -> RunContext:
    return RunContext(run_id=env["dao"].create_run(
        env["sid"], env["uid"], "帮我看看", intent=intent,
        deadline_sec=deadline_sec),
        session_id=env["sid"], user_id=env["uid"], role="safety",
        intent=intent, user_input="帮我看看", deadline_sec=deadline_sec)


_TERMINAL = ("completed", "degraded", "failed", "cancelled")


def _wait_status(db_file: str, run_id: str, statuses, timeout=10.0) -> str:
    conn = get_conn(db_file)
    try:
        dao = AgentChatDAO(conn)
        t0 = time.time()
        last = ""
        while time.time() - t0 < timeout:
            last = dao.get_run(run_id)["status"]
            if last in statuses:
                return last
            time.sleep(0.05)
        return last
    finally:
        conn.close()


# ---------- §9.1 组合 1：云端可用（正常态）→ run completed 无降级 ----------

def test_combo1_cloud_ok_run_completed(env):
    client = FakeChatClient([
        ChatResult(content=_ok_plan([_echo_step()]), status="success"),
        ChatResult(content="云端汇总", status="success"),
    ])
    ctx = _ctx(env)
    out = PlanExecutor(env["dao"], client, _mk_registry()).run(ctx)
    assert out.status == "completed"
    assert out.answer == "云端汇总"
    result = json.loads(out.result_json)
    assert result["degraded_reason"] is None          # 全链无降级
    steps = _steps_of(env, ctx.run_id)
    assert all(s["status"] == "success" for s in steps)


# ---------- §9.1 组合 2：云端超时落本地 → degraded 且计划收紧 ≤4 ----------

def test_combo2_cloud_timeout_local_degraded(env):
    six = [_echo_step(f"步{i}") for i in range(6)]
    client = FakeChatClient([
        ChatResult(content=_ok_plan(six), status="degraded"),   # 本地档出计划
        ChatResult(content="本地档汇总", status="degraded"),
    ])
    ctx = _ctx(env)
    out = PlanExecutor(env["dao"], client, _mk_registry()).run(ctx)
    assert out.status == "degraded"
    steps = _steps_of(env, ctx.run_id)
    assert len(steps) == 4                            # 收紧 ≤4（§7）
    # 本地档规划调用显式携带 max_tokens=1024（§7 num_predict 修正，
    # ChatClient._call_local 据此传 num_predict=1024）
    assert client.calls[0]["max_tokens"] == 1024


# ---------- §9.1 组合 3：全档失败 + 剧本 → 规则模板档 degraded ----------

def test_combo3_all_failed_with_playbook_template(env):
    client = FakeChatClient([
        ChatResult(content=None, status="failed", error="云端超时"),
        ChatResult(content=None, status="failed", error="本地不可用"),
    ])
    ctx = _ctx(env, intent="generic")                 # 通用剧本有模板档
    out = PlanExecutor(env["dao"], client, _mk_registry()).run(ctx)
    assert out.status == "degraded"                   # degraded ≠ failed
    assert out.answer == default_template_answer("帮我看看")
    result = json.loads(out.result_json)
    assert "规划失败" in result["degraded_reason"]


# ---------- §9.1 组合 4：全档失败 + 模板不适用 → failed 但可读 ----------

def test_combo4_all_failed_no_playbook_human_fallback(env):
    client = FakeChatClient([
        ChatResult(content=None, status="failed", error="云端不可用"),
        ChatResult(content=None, status="failed", error="本地不可用"),
    ])
    ctx = _ctx(env, intent=None)                      # 无剧本 → 无模板可落
    out = PlanExecutor(env["dao"], client, _mk_registry()).run(ctx)
    assert out.status == "failed"
    assert "规划失败" in (out.error or "")
    assert "本地不可用" in (out.error or "")           # 失败留因（可读，交人工）


# ---------- §9.1 组合 5：非法 JSON×2 → 重试 ≤1 次后落规则模板档 ----------

def test_combo5_invalid_json_twice_falls_to_template(env):
    client = FakeChatClient([
        ChatResult(content="{截断的残缺 JSON", status="success"),
        ChatResult(content="这不是 JSON", status="success"),
    ])
    ctx = _ctx(env, intent="generic")
    out = PlanExecutor(env["dao"], client, _mk_registry()).run(ctx)
    assert out.status == "degraded"                   # 模板档兜底
    assert len(client.calls) == 2                     # 首次 + 重试 ≤1，无第三次
    assert out.answer == default_template_answer("帮我看看")


# ---------- §9.2 慢工具注入 → 墙钟强制收敛且整体 degraded ----------

def test_slow_tool_forces_convergence_degraded(env):
    client = FakeChatClient([
        ChatResult(content=_ok_plan(
            [_echo_step(), {"tool": "slow", "args": {}, "reason": "慢"},
             _echo_step("第三步")]), status="success"),
    ])
    ctx = _ctx(env, deadline_sec=1.6)
    out = PlanExecutor(env["dao"], client,
                       _mk_registry(sleep_sec=1.2)).run(ctx)
    assert out.status == "degraded"
    assert "收敛" in (out.error or "")
    steps = _steps_of(env, ctx.run_id)
    assert [s["tool"] for s in steps] == ["echo", "slow"]   # 第三步未执行
    assert len(client.calls) == 1                     # 无汇总调用（预算耗尽）


# ---------- §9.3 模拟进程重启：挂起态不被孤儿误伤、可恢复续跑 ----------

def test_pending_confirm_survives_restart_scan_and_resumes(env, monkeypatch):
    pay_calls = {"n": 0}

    def _pay(args, ctx):
        pay_calls["n"] += 1
        return {"status": "success", "data": {"queued": True}}

    registry = {**_mk_registry(),
                "pay": ToolSpec(fn=_pay, desc="副作用", args_schema=NoArgs,
                                side_effect=True)}

    class _PlanClient:
        def chat(self, system, user, *, json_schema=None, max_tokens=1024,
                 total_deadline_sec=30.0, provider=None) -> ChatResult:
            if json_schema is not None:
                return ChatResult(content=_ok_plan(
                    [_echo_step(), {"tool": "pay", "args": {}, "reason": "建"}]),
                    status="success")
            return ChatResult(content="确认后完成", status="success")

    monkeypatch.setattr(PlanRunService, "_REGISTRY", registry)
    monkeypatch.setattr(PlanRunService, "_CHAT_FACTORY",
                        lambda: _PlanClient())
    with PlanRunService._RUN_LOCK:
        PlanRunService._active_runs.clear()
    try:
        rid = PlanRunService.create_run(env["uid"], env["sid"], "建个草稿")
        assert _wait_status(env["db_file"], rid,
                            ("pending_confirm",)) == "pending_confirm"
        assert pay_calls["n"] == 0

        # 模拟重启遗留：把 updated_at 拨旧（超过孤儿阈值），再扫描——
        # pending_confirm 必须保持挂起有效（确认卡与计划都在库中，§5.6.3）
        env["conn"].execute(
            "UPDATE agent_chat_runs "
            "SET updated_at=datetime('now','-5 minutes') WHERE id=?", (rid,))
        env["conn"].commit()
        assert PlanRunService.scan_orphans() == 0
        row = AgentChatDAO(get_conn(env["db_file"])).get_run(rid)
        assert row["status"] == "pending_confirm"
        assert json.loads(row["confirm_payload"])["tool"] == "pay"

        # 重启后确认恢复：原子翻转 → 从 current_step_idx+1 续跑至终态
        res = PlanRunService.confirm(rid, env["uid"], "confirm")
        assert res["status"] == "running"
        assert _wait_status(env["db_file"], rid, _TERMINAL) == "completed"
        assert pay_calls["n"] == 1                    # 幂等：副作用仅一次
    finally:
        with PlanRunService._RUN_LOCK:
            PlanRunService._active_runs.clear()


# ---------- 辅助 ----------

def _steps_of(env, run_id: str) -> list[dict]:
    return env["dao"].list_steps(run_id)
