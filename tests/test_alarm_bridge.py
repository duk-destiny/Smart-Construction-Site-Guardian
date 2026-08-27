"""告警→工单桥测试（v0.7）：轻链路产物进派发闭环的最后一块。

覆盖：severity 查级映射(critical→较大)、task.source=camera、审计互引、
告警状态转 confirmed、重复转换/已处置告警拒绝。
"""
from __future__ import annotations

import pytest

from dao.db import get_conn, init_db
from dao.models import AlarmEventDAO, RiskDAO, TaskDAO, UserDAO, WorkOrderDAO
from services.dispatch_service import DispatchService
from services.permission_service import PermissionError


@pytest.fixture
def env():
    conn = get_conn(":memory:")
    init_db(conn)
    users = UserDAO(conn)
    admin = users.insert("admin", "hashed", "admin")
    lisi = users.insert("lisi", "hashed", "responsible")
    alarms = AlarmEventDAO(conn)
    spark = alarms.insert("s1", None, "hot_work", "spark", 0.93,
                          source="rtsp://cam1", status="new")
    resolved = alarms.insert("s2", None, "hot_work", "smoke", 0.9,
                             source="rtsp://cam1", status="resolved")
    conn.commit()
    return {"conn": conn,
            "svc": DispatchService(conn, rules=[{"scene": "hot_work",
                                                 "assignee": "lisi"}]),
            "ids": {"admin": admin, "lisi": lisi},
            "alarms": alarms, "spark": spark, "resolved": resolved}


def test_convert_maps_severity_and_links_camera_source(env):
    oid = env["svc"].convert_alarm_to_order(env["spark"], env["ids"]["admin"])
    wo = env["svc"].orders.get(oid)
    task = env["conn"].execute("SELECT * FROM tasks WHERE id=?",
                               (wo["task_id"],)).fetchone()
    assert task["source"] == "camera"
    assert wo["risk_level"] == "较大"                       # spark=critical
    assert "[实时告警]" in wo["hazard_desc"]
    risk = env["conn"].execute("SELECT risk_level FROM risks WHERE task_id=?",
                               (wo["task_id"],)).fetchone()[0]
    assert risk == "较大"


def test_alarm_marked_confirmed_and_audit_crossref(env):
    oid = env["svc"].convert_alarm_to_order(env["spark"], env["ids"]["admin"])
    row = env["alarms"].get_by_id(env["spark"])
    assert row["status"] == "confirmed"
    import json
    details = [json.loads(r["detail_json"]) for r in env["conn"].execute(
        "SELECT detail_json FROM audit_logs WHERE action='alarm_to_order'")]
    assert details[0]["alarm_id"] == env["spark"]
    assert details[0]["order_id"] == oid


def test_double_convert_rejected(env):
    env["svc"].convert_alarm_to_order(env["spark"], env["ids"]["admin"])
    with pytest.raises(ValueError, match="已转为工单"):
        env["svc"].convert_alarm_to_order(env["spark"], env["ids"]["admin"])


def test_resolved_alarm_cannot_convert(env):
    with pytest.raises(ValueError, match="resolved"):
        env["svc"].convert_alarm_to_order(env["resolved"], env["ids"]["admin"])


def test_responsible_cannot_convert(env):
    with pytest.raises(PermissionError):
        env["svc"].convert_alarm_to_order(env["spark"], env["ids"]["lisi"])


def test_bridged_order_flows_into_dispatch(env):
    """桥接后的工单可直接走既有派发闭环（回归关键路径）。"""
    oid = env["svc"].convert_alarm_to_order(env["spark"], env["ids"]["admin"])
    tid = env["svc"].orders.get(oid)["task_id"]
    env["svc"].dispatch_order(tid, env["ids"]["admin"], scene_id="hot_work")
    assert env["svc"].orders.get(oid)["assignee_id"] == env["ids"]["lisi"]
