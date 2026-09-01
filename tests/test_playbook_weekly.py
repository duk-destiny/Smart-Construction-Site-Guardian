"""周报剧本测试（设计文档 §5.9/§9）：Fake ChatClient 注入 + 文件库种子，
事实源为真实 `WeeklyReportService.gather()`，零网络。

覆盖：
- 合格汇总（数字+键路径）→ 校验通过、completed、回溯率 100%；
- 数字与键路径不符 → 触发一次重写 → 二次合格；二次仍不合格 → 模板档 degraded；
- ChatClient 全 failed → 模板档兜底产出非空且含关键数字；
- 校验器单元（漏标注/键路径不存在/数值不符/百分号容忍）；
- 意图规则检测 → weekly_report 路由到剧本（端到端经 PlanRunService）。
"""
from __future__ import annotations

import json
import time

import pytest

from core.chat_client import ChatResult
from dao.db import get_conn, init_db
from dao.models import AgentChatDAO, UserDAO
from services.agent.kernel import PlanExecutor
from services.agent.models import RunContext
from services.agent.playbooks import (detect_intent, extract_weekly_citations,
                                      get_playbook, traceback_rate,
                                      validate_weekly_narrative,
                                      weekly_template_answer)
from services.agent.tools import ToolSpec, WeeklyReportArgs
from services.report_service import WeeklyReportService


# ---------- Fake ChatClient（脚本化应答，记录调用）----------

class FakeChatClient:
    def __init__(self, replies: list):
        self.replies = list(replies)
        self.calls: list[dict] = []

    def chat(self, system, user, *, json_schema=None, max_tokens=1024,
             total_deadline_sec=30.0, provider=None) -> ChatResult:
        self.calls.append({"json_schema": json_schema, "system": system,
                           "user": user})
        if not self.replies:
            return ChatResult(content=None, status="failed",
                              error="脚本应答耗尽")
        r = self.replies.pop(0)
        if callable(r):
            r = r()
        return r


# ---------- 种子：文件库 + 确定性事实源 ----------

@pytest.fixture()
def env(monkeypatch, tmp_path):
    import dao.db as dao_db

    db_file = str(tmp_path / "weekly_playbook.db")
    monkeypatch.setattr(dao_db, "DEFAULT_DB_PATH", db_file)
    conn = get_conn(db_file)
    init_db(conn)
    uid = UserDAO(conn).insert("alice", "hash", "safety")

    # 检测帧 5 条：不合规 3（spark×2 + smoke×1）/ 警告 1 / 合规 1
    frames = [
        ("r0", "spark", "不合规"), ("r1", "spark", "不合规"),
        ("r2", "smoke", "不合规"), ("r3", "none", "警告"),
        ("r4", "none", "合规"),
    ]
    for rid, cls, status in frames:
        conn.execute(
            "INSERT INTO detection_records(id,session_id,scene_id,mode,"
            "frame_status,cls,conf,severity,track_id,track_frames,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (rid, "s0", None, "realtime", status, cls, 0.9,
             "critical" if cls != "none" else "safe", None, 1,
             "2030-01-05 10:00:00"))

    # 告警：新建态 1 条
    conn.execute(
        "INSERT INTO alarm_events(id,session_id,task_id,scene_id,cls,conf,"
        "image_path,source,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        ("al1", None, None, "hot_work", "spark", 0.9, None, "rtsp_bg",
         "new", "2030-01-06 09:00:00"))

    # 工单 2 张：已销项 1 + 在办逾期 1（责任人均为 alice）
    wo_seed = [
        ("w1", "closed", "2040-01-01 00:00:00"),
        ("w2", "open", "2020-01-01 00:00:00"),     # deadline 已过 → 存量逾期
    ]
    for wid, status, deadline in wo_seed:
        conn.execute(
            "INSERT INTO tasks(id,user_id,permit_json,status,source,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (f"t_{wid}", uid, "{}", "completed", "upload",
             "2030-01-04 10:00:00"))
        conn.execute(
            "INSERT INTO work_orders(id,task_id,hazard_desc,risk_level,"
            "worker_notice,assignee_id,status,deadline,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (wid, f"t_{wid}", "隐患", "一般", "", uid, status, deadline,
             "2030-01-04 11:00:00"))
    conn.commit()

    dao = AgentChatDAO(conn)
    sid = dao.create_session(uid)
    stats = WeeklyReportService(conn).gather()
    return {"conn": conn, "dao": dao, "uid": uid, "sid": sid,
            "stats": stats, "monkeypatch": monkeypatch}


def _mk_registry() -> dict[str, ToolSpec]:
    """周报统计工具（薄封装真实 gather，跳过权限层——内核测试只测剧本链路）。"""

    def _weekly(args, ctx):
        from services.agent.playbooks import fetch_weekly_stats
        return {"status": "success",
                "data": fetch_weekly_stats(args.get("start"),
                                           args.get("end")),
                "error": None}

    return {"weekly_report_data": ToolSpec(
        fn=_weekly, desc="周报统计", args_schema=WeeklyReportArgs)}


def _disable_weekly_preset(env) -> None:
    """临时禁用 weekly 预置计划（plan_fn=None），按 v1.0 LLM 规划路径测剧本。"""
    from dataclasses import replace

    import services.agent.playbooks as pbm
    env["monkeypatch"].setattr(pbm, "_PLAYBOOKS", {
        **pbm._PLAYBOOKS,
        "weekly_report": replace(pbm._PLAYBOOKS["weekly_report"], plan_fn=None),
    })


_PLAN = {"goal": "生成安全周报",
         "steps": [{"tool": "weekly_report_data", "args": {},
                    "reason": "取确定性统计"}]}


def _good_answer(stats: dict) -> str:
    """合格稿：每个数字均带正确键路径标注。"""
    return (f"检测概况：周期内检测帧 {stats['frames']}（来源：frames），"
            f"其中不合规 {stats['bad']}（来源：bad）。"
            f"工单闭环：新增工单 {stats['orders_total']}（来源：orders_total），"
            f"已销项 {stats['orders_by_status']['closed']}"
            "（来源：orders_by_status.closed），"
            f"逾期未整改 {stats['overdue_open_now']}（来源：overdue_open_now）。")


_BAD_NO_CITE = "周期内检测帧共 5 张，未标注任何来源。"


def _bad_wrong_value(stats: dict) -> str:
    return (f"已销项 99（来源：orders_by_status.closed），"
            f"检测帧 {stats['frames']}（来源：frames）。")


def _bad_unknown_path() -> str:
    return "已销项 1（来源：orders_by_status.不存在的键）。"


def _mk_run(env, user_input: str = "出一份本周安全周报") -> tuple:
    rid = env["dao"].create_run(env["sid"], env["uid"], user_input,
                                intent="weekly_report")
    assert env["dao"].transition_status(rid, "pending", "running")
    ctx = RunContext(run_id=rid, session_id=env["sid"], user_id=env["uid"],
                     role="safety", intent="weekly_report",
                     user_input=user_input, deadline_sec=30.0)
    return rid, ctx


# ---------- 合格用例：校验通过 + 回溯率 100% ----------

def test_synth_valid_passes_and_completed(env):
    _disable_weekly_preset(env)
    stats = env["stats"]
    good = _good_answer(stats)
    client = FakeChatClient([
        ChatResult(content=_PLAN, status="success"),
        ChatResult(content=good, status="success"),
    ])
    rid, ctx = _mk_run(env)
    out = PlanExecutor(env["dao"], client, _mk_registry()).run(ctx)
    assert out.status == "completed"
    assert out.answer == good
    payload = json.loads(out.result_json)
    assert payload["traceback_verified"] is True
    # 回溯率断言 100%：每个正文数字都映射到真实键值
    assert traceback_rate(good) == 1.0
    ok, errors = validate_weekly_narrative(good, stats)
    assert ok and errors == []
    # 每个标注键路径都能在 gather() 字典中解析出与正文一致的值
    from services.agent.playbooks import flatten_stats
    flat = flatten_stats(stats)
    for num_s, path, _pct in extract_weekly_citations(good):
        assert float(num_s) == float(flat[path])


# ---------- 不合格 → 重写一次 → 二次合格 ----------

def test_synth_rewrite_once_then_passes(env):
    _disable_weekly_preset(env)
    stats = env["stats"]
    good = _good_answer(stats)
    client = FakeChatClient([
        ChatResult(content=_PLAN, status="success"),
        ChatResult(content=_bad_wrong_value(stats), status="success"),
        ChatResult(content=good, status="success"),
    ])
    rid, ctx = _mk_run(env)
    out = PlanExecutor(env["dao"], client, _mk_registry()).run(ctx)
    assert out.status == "completed"
    assert out.answer == good
    # 1 规划 + 2 汇总（首次不合格 + 重写一次）
    assert len(client.calls) == 3
    assert "未通过键路径回溯校验" in client.calls[2]["user"]


# ---------- 二次仍不合格 → 落模板档记 degraded ----------

def test_synth_two_failures_falls_to_template(env):
    _disable_weekly_preset(env)
    stats = env["stats"]
    client = FakeChatClient([
        ChatResult(content=_PLAN, status="success"),
        ChatResult(content=_BAD_NO_CITE, status="success"),
        ChatResult(content=_bad_unknown_path(), status="success"),
    ])
    rid, ctx = _mk_run(env)
    out = PlanExecutor(env["dao"], client, _mk_registry()).run(ctx)
    assert out.status == "degraded"
    assert "模板档" in (out.error or "")
    assert out.answer and "规则模板档" in out.answer
    # 模板档同样携带关键数字且满足回溯校验
    assert str(stats["frames"]) in out.answer
    assert str(stats["orders_by_status"]["closed"]) in out.answer
    ok, errors = validate_weekly_narrative(out.answer, stats)
    assert ok, errors
    assert traceback_rate(out.answer) == 1.0


# ---------- ChatClient 全 failed → 模板档兜底 ----------

def test_llm_all_failed_template_fallback(env):
    """规划两次全败 → 剧本规则模板档作答（降级矩阵第 3 档）。"""
    _disable_weekly_preset(env)
    stats = env["stats"]
    client = FakeChatClient([
        ChatResult(content=None, status="failed", error="云端不可用"),
        ChatResult(content=None, status="failed", error="本地档不可用"),
    ])
    rid, ctx = _mk_run(env)
    out = PlanExecutor(env["dao"], client, _mk_registry()).run(ctx)
    assert out.status == "degraded"
    assert out.answer and len(out.answer) > 0
    # 非空且含关键数字（gather 真值）+ 来源标注
    assert str(stats["frames"]) in out.answer
    assert str(stats["orders_total"]) in out.answer
    assert "（来源：orders_by_status.closed）" in out.answer


# ---------- 模板档直接单测 ----------

def test_template_answer_non_empty_with_key_numbers(env):
    stats = env["stats"]
    answer = weekly_template_answer("出一份本周安全周报")
    assert answer and "规则模板档" in answer
    for key_num in (stats["frames"], stats["bad"], stats["orders_total"],
                    stats["orders_by_status"]["closed"],
                    stats["overdue_open_now"]):
        assert str(key_num) in answer
    ok, errors = validate_weekly_narrative(answer, stats)
    assert ok, errors
    assert traceback_rate(answer) == 1.0


# ---------- 校验器单元 ----------

def test_validator_rejects_uncited_number(env):
    ok, errors = validate_weekly_narrative(_BAD_NO_CITE, env["stats"])
    assert not ok
    assert any("未标注来源键路径" in e for e in errors)


def test_validator_rejects_unknown_path(env):
    ok, errors = validate_weekly_narrative(_bad_unknown_path(), env["stats"])
    assert not ok
    assert any("键路径不存在" in e for e in errors)


def test_validator_rejects_wrong_value(env):
    ok, errors = validate_weekly_narrative(_bad_wrong_value(env["stats"]),
                                           env["stats"])
    assert not ok
    assert any("数值不符" in e for e in errors)


def test_validator_accepts_percent_of_rate_field(env):
    """百分号标注允许 = *_rate 字段 ×100 的展示形式。"""
    rate = env["stats"]["per_assignee"][0]["overdue_rate"]
    text = f"责任人逾期率 {rate * 100}%（来源：per_assignee[0].overdue_rate）。"
    ok, errors = validate_weekly_narrative(text, env["stats"])
    assert ok, errors


def test_validator_ignores_date_strings(env):
    """统计周期日期串不计入待标注数字。"""
    stats = env["stats"]
    text = (f"统计周期 2030-01-04 ~ 2030-01-10，检测帧 "
            f"{stats['frames']}（来源：frames）。")
    ok, errors = validate_weekly_narrative(text, stats)
    assert ok, errors


# ---------- v2.2 确定性预置计划（weekly plan_fn）----------

def _seed_current_week(env) -> tuple[str, str, dict]:
    """种本周检测帧 + 已销项工单，返回 (start, end, 本周口径 gather 统计)。"""
    from datetime import date, datetime, timedelta

    conn = env["conn"]
    today = date.today()
    start = today - timedelta(days=today.weekday())
    end = start + timedelta(days=6)
    ts = f"{datetime.now():%Y-%m-%d %H:%M:%S}"
    conn.execute(
        "INSERT INTO detection_records(id,session_id,scene_id,mode,"
        "frame_status,cls,conf,severity,track_id,track_frames,created_at) "
        "VALUES('tw1','s0',NULL,'realtime','不合规','spark',0.9,'critical',"
        "NULL,1,?)", (ts,))
    conn.execute(
        "INSERT INTO tasks(id,user_id,permit_json,status,source,created_at) "
        "VALUES('t_tw',?,'{}','completed','upload',?)", (env["uid"], ts))
    conn.execute(
        "INSERT INTO work_orders(id,task_id,hazard_desc,risk_level,"
        "worker_notice,assignee_id,status,deadline,created_at) "
        "VALUES('w_tw','t_tw','隐患','一般','',?,'closed',?,?)",
        (env["uid"], "2040-01-01 00:00:00", ts))
    conn.commit()
    from services.report_service import WeeklyReportService
    stats = WeeklyReportService(conn).gather(start.isoformat(), end.isoformat())
    return start.isoformat(), end.isoformat(), stats


def test_weekly_preset_plan_dates_and_single_llm_call(env):
    """plan_fn 预置计划：日期由代码按今天计算（本周一~周日），规划零 LLM
    （仅汇总一次调用），汇总合格即 completed。"""
    start, end, stats_now = _seed_current_week(env)
    client = FakeChatClient([
        ChatResult(content=_good_answer(stats_now), status="success"),
    ])
    rid, ctx = _mk_run(env)
    out = PlanExecutor(env["dao"], client, _mk_registry()).run(ctx)
    assert out.status == "completed"
    assert out.answer == _good_answer(stats_now)
    # 规划零 LLM：仅汇总一次调用，且不带 JSON schema
    assert len(client.calls) == 1 and client.calls[0]["json_schema"] is None
    plan = json.loads(env["dao"].get_run(rid)["plan_json"])
    assert plan["steps"][0]["tool"] == "weekly_report_data"
    assert plan["steps"][0]["args"] == {"start": start, "end": end}


# ---------- 意图接线：规则检测 → 剧本路由（端到端）----------

def test_detect_intent_weekly():
    assert detect_intent("出一份本周安全周报") == "weekly_report"
    assert detect_intent("帮我生成周报") == "weekly_report"
    assert detect_intent("查一下工单进度") is None
    assert get_playbook(detect_intent("来份周报")) is not None


def test_run_service_routes_weekly_intent_to_playbook(env):
    """PlanRunService 端到端（v2.2 预置计划）：日期由代码计算、规划零 LLM、
    剧本预算 + 助手消息含来源标注。"""
    from services.agent.run_service import PlanRunService

    env["monkeypatch"].setattr(PlanRunService, "_REGISTRY", _mk_registry())
    start_iso, end_iso, stats_now = _seed_current_week(env)
    good = _good_answer(stats_now)

    class _Fake(FakeChatClient):
        def chat(self, system, user, *, json_schema=None, max_tokens=1024,
                 total_deadline_sec=30.0, provider=None) -> ChatResult:
            self.calls.append({"json_schema": json_schema})
            assert json_schema is None          # 预置计划：规划零 LLM 调用
            return ChatResult(content=good, status="success")

    env["monkeypatch"].setattr(PlanRunService, "_CHAT_FACTORY",
                               lambda: _Fake([]))
    with PlanRunService._RUN_LOCK:
        PlanRunService._active_runs.clear()

    rid = PlanRunService.create_run(env["uid"], env["sid"],
                                    "出一份本周安全周报")
    dao = AgentChatDAO(env["conn"])
    t0 = time.time()
    status = ""
    while time.time() - t0 < 10:
        status = dao.get_run(rid)["status"]
        if status in ("completed", "degraded", "failed", "cancelled"):
            break
        time.sleep(0.05)
    assert status == "completed"
    row = dao.get_run(rid)
    assert row["intent"] == "weekly_report"
    plan = json.loads(row["plan_json"])
    assert plan["steps"][0]["args"] == {"start": start_iso, "end": end_iso}
    assert float(row["deadline_sec"]) == 120.0     # 剧本级墙钟预算
    msgs = dao.list_messages(env["sid"])
    asst = [m for m in msgs if m["role"] == "assistant" and m["run_id"] == rid]
    assert asst and "（来源：orders_by_status.closed）" in asst[0]["content"]

    with PlanRunService._RUN_LOCK:
        PlanRunService._active_runs.clear()
