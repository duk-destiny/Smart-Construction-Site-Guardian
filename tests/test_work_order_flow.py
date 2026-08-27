"""工单闭环全生命周期测试（v0.2 P0）。

覆盖：派发规则解析、权限边界（override/rectify/非本人）、状态机流转
（open→submitted→closed；驳回退回 open 再改）、逾期巡检计数与越级、
任务来源标记进台账。时间相关断言用固定字面量时刻，保证确定性。
"""
from __future__ import annotations

import pytest

from dao.db import get_conn, init_db
from dao.models import (
    AuditDAO, RiskDAO, TaskDAO, UserDAO, WorkOrderDAO,
)
from services.dispatch_service import (
    DispatchService, RISK_DEADLINE_HOURS, _hours_between, _now_str,
)
from services.permission_service import PermissionError

FIXED_NOW = "2030-01-10 12:00:00"


@pytest.fixture
def env():
    """内存库 + 四个账号（admin/safety/lisi/wangwu）+ 一条已生成工单的任务。"""
    conn = get_conn(":memory:")
    init_db(conn)
    users = UserDAO(conn)
    admin = users.insert("admin", "hashed", "admin")
    safety = users.insert("zhangsan", "hashed", "safety")
    lisi = users.insert("lisi", "hashed", "responsible")
    wangwu = users.insert("wangwu", "hashed", "responsible")

    tasks = TaskDAO(conn)
    tid = tasks.insert(safety, "{}", "completed", source="upload")

    RiskDAO(conn).insert(tid, "较大", "[]", "[]")
    WorkOrderDAO(conn).insert(
        task_id=tid, hazard_desc="动火区堆放易燃纸箱",
        clause="第X条", requirement="立即清理并配备监火人",
        risk_level="较大", worker_notice="模板文案")

    rules = [{"scene": "hot_work", "assignee": "lisi"}]
    return {
        "conn": conn, "users": users, "tasks": tasks,
        "audit": AuditDAO(conn),
        "svc": DispatchService(conn, rules=rules),
        "ids": {"admin": admin, "safety": safety, "lisi": lisi, "wangwu": wangwu},
        "task_id": tid,
    }


def _task(env, source="upload"):
    tid = env["tasks"].insert(env["ids"]["safety"], "{}", "completed", source=source)
    RiskDAO(env["conn"]).insert(tid, "一般", "[]", "[]")
    WorkOrderDAO(env["conn"]).insert(
        task_id=tid, hazard_desc=f"隐患{tid[-4:]}", clause=None,
        requirement="限期整改", risk_level="一般", worker_notice="")
    return tid


def _actions(env):
    return [r["action"] for r in env["conn"].execute(
        "SELECT action FROM audit_logs").fetchall()]


# ---------- 派发 ----------

def test_rule_resolution_first_match_with_scene(env):
    assert env["svc"].resolve_assignee(scene_id="hot_work") == "lisi"
    # 未配置的场景：无 scene 键的规则可作通配兜底；当前规则集均有 scene → 无命中
    assert env["svc"].resolve_assignee(scene_id="unknown_scene") is None


def test_dispatch_by_rule_sets_fields_and_audits(env):
    oid = env["svc"].dispatch_order(env["task_id"], env["ids"]["safety"],
                                    scene_id="hot_work")
    row = env["svc"].orders.get(oid)
    assert row["assignee_id"] == env["ids"]["lisi"]
    assert row["status"] == "open"
    assert row["deadline"] and len(row["deadline"]) >= 19
    # 默认时限按风险等级「较大」→ 2h
    assert _hours_between(row["deadline"], row["dispatched_at"]) \
        == pytest.approx(RISK_DEADLINE_HOURS["较大"])
    assert "dispatch" in _actions(env)


def test_dispatch_requires_override_permission(env):
    with pytest.raises(PermissionError):
        env["svc"].dispatch_order(env["task_id"], env["ids"]["lisi"],
                                  assignee_username="wangwu")


def test_dispatch_rejects_non_responsible_target(env):
    with pytest.raises(ValueError, match="responsible"):
        env["svc"].dispatch_order(env["task_id"], env["ids"]["safety"],
                                  assignee_username="admin")


def test_cannot_redispatch_while_awaiting_review(env):
    svc = env["svc"]
    svc.dispatch_order(env["task_id"], env["ids"]["safety"], scene_id="hot_work")
    order = svc.orders.get_by_task(env["task_id"])
    svc.submit_rectification(order["id"], env["ids"]["lisi"], "已清理并复检")
    with pytest.raises(ValueError, match="submitted"):
        svc.dispatch_order(env["task_id"], env["ids"]["safety"],
                           assignee_username="wangwu")


# ---------- 整改提交 ----------

def test_submit_by_assignee_moves_to_submitted(env):
    svc = env["svc"]
    svc.dispatch_order(env["task_id"], env["ids"]["safety"], scene_id="hot_work")
    order = svc.orders.get_by_task(env["task_id"])
    svc.submit_rectification(order["id"], env["ids"]["lisi"], "清理完成附照片",
                             ["data/rectifications/x/1.jpg"])
    assert svc.orders.get(order["id"])["status"] == "submitted"
    assert "rectification_submit" in _actions(env)


def test_other_responsible_cannot_submit(env):
    svc = env["svc"]
    svc.dispatch_order(env["task_id"], env["ids"]["safety"], scene_id="hot_work")
    order = svc.orders.get_by_task(env["task_id"])
    with pytest.raises(PermissionError, match="本单责任人"):
        svc.submit_rectification(order["id"], env["ids"]["wangwu"], "冒名提交")


def test_safety_cannot_submit_rectification(env):
    svc = env["svc"]
    svc.dispatch_order(env["task_id"], env["ids"]["safety"], scene_id="hot_work")
    order = svc.orders.get_by_task(env["task_id"])
    with pytest.raises(PermissionError):
        svc.submit_rectification(order["id"], env["ids"]["safety"], "安全员代办")


# ---------- 验收 ----------

def test_reject_requires_reason_and_returns_to_open(env):
    svc = env["svc"]
    svc.dispatch_order(env["task_id"], env["ids"]["safety"], scene_id="hot_work")
    order = svc.orders.get_by_task(env["task_id"])
    svc.submit_rectification(order["id"], env["ids"]["lisi"], "第一次提交")
    with pytest.raises(ValueError, match="原因"):
        svc.review_order(order["id"], env["ids"]["admin"], approve=False)
    svc.review_order(order["id"], env["ids"]["admin"], approve=False, reason="现场仍有残留")
    after = svc.orders.get(order["id"])
    assert after["status"] == "open"
    assert after["review_reason"] == "现场仍有残留"


def test_full_lifecycle_open_to_closed(env):
    svc = env["svc"]
    svc.dispatch_order(env["task_id"], env["ids"]["safety"], scene_id="hot_work")
    order = svc.orders.get_by_task(env["task_id"])
    svc.submit_rectification(order["id"], env["ids"]["lisi"], "第一版")
    svc.review_order(order["id"], env["ids"]["admin"], False, reason="照片模糊重传")
    svc.submit_rectification(order["id"], env["ids"]["lisi"], "第二版含清晰照片")
    svc.review_order(order["id"], env["ids"]["admin"], True)
    final = svc.orders.get(order["id"])
    assert final["status"] == "closed"
    assert final["approved_by"] == env["ids"]["admin"]
    actions = _actions(env)
    assert actions.count("review") == 2 and actions.count("rectification_submit") == 2


def test_only_admin_can_review_but_not_responsible(env):
    svc = env["svc"]
    svc.dispatch_order(env["task_id"], env["ids"]["safety"], scene_id="hot_work")
    order = svc.orders.get_by_task(env["task_id"])
    svc.submit_rectification(order["id"], env["ids"]["lisi"], "提交")
    with pytest.raises(PermissionError):
        svc.review_order(order["id"], env["ids"]["lisi"], True)


# ---------- 逾期巡检（固定时间游标，确定性） ----------

def _make_overdue(env, task_id, deadline):
    svc = env["svc"]
    svc.dispatch_order(task_id, env["ids"]["safety"], scene_id="hot_work",
                       deadline_hours=1)
    # 直接覆写截止时间为固定历史时刻，避免真实时钟波动影响断言
    env["conn"].execute("UPDATE work_orders SET deadline=? WHERE task_id=?",
                        (deadline, task_id))
    env["conn"].commit()


def test_scan_overdue_counts_notify_and_escalate(env):
    svc = env["svc"]
    t_fast = env["task_id"]
    t_slow = _task(env)
    t_closed_base = _task(env)
    _make_overdue(env, t_fast, "2030-01-10 07:00:00")   # 逾期 5h：仅催办
    _make_overdue(env, t_slow, "2030-01-09 06:00:00")   # 逾期 30h：催办+升级
    _make_overdue(env, t_closed_base, "2030-01-08 00:00:00")
    _order = svc.orders.get_by_task(t_closed_base)
    env["svc"].orders.set_reviewed(_order["id"], True, env["ids"]["admin"])

    res = svc.scan_overdue(as_of=FIXED_NOW)
    assert res["overdue"] == 2      # 已销项不计入
    assert res["notified"] == 2
    assert res["escalated"] == 1    # 仅 30h 那单触发越级
    assert res["as_of"] == FIXED_NOW


def test_scan_is_deterministic_on_same_cursor(env):
    svc = env["svc"]
    _make_overdue(env, env["task_id"], "2030-01-10 11:00:00")
    r1 = svc.scan_overdue(as_of=FIXED_NOW)
    r2 = svc.scan_overdue(as_of=FIXED_NOW)
    assert r1 == r2                 # 纯函数：同游标同结果（审计行会重复，属预期流水）


# ---------- 任务来源与台账 ----------

def test_task_source_recorded_and_visible_in_ledger(env):
    from datetime import datetime, timedelta, timezone
    text_task = env["tasks"].insert(env["ids"]["safety"], "{}", "pending",
                                    source="text")
    RiskDAO(env["conn"]).insert(text_task, "一般", "[]", "[]")
    WorkOrderDAO(env["conn"]).insert(
        task_id=text_task, hazard_desc="文字上报隐患", clause=None,
        requirement="核实", risk_level="一般", worker_notice="")
    rows = {r["task_id"]: r for r in
            WorkOrderDAO(env["conn"]).list_all_with_risk()}
    assert rows[text_task]["source"] == "text"
    assert rows[env["task_id"]]["source"] == "upload"


def test_overdue_view_uses_utc_consistent_format(env):
    """deadline 与游标同为 '%Y-%m-%d %H:%M:%S' 口径，字符串比较即时间比较。"""
    early = _now_str(-48)
    late = _now_str(0)
    assert early < late
