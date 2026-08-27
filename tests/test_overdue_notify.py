"""催办 webhook 化测试（v0.6 二期b）：scan_overdue 推送管线。

fake notifier 收集调用断言双档文案（责任人+越级）；notify 未启用时
skipped 计数与审计流水并行不悖；扫描器对数据库保持只读+审计增量。
"""
from __future__ import annotations

import pytest

from dao.db import get_conn, init_db
from dao.models import AuditDAO, RiskDAO, TaskDAO, UserDAO, WorkOrderDAO
from services.dispatch_service import DispatchService

FIXED_NOW = "2030-02-01 12:00:00"


class FakeNotifier:
    def __init__(self):
        self.calls = []

    def push_overdue(self, order_id, assignee, hazard, deadline,
                     overdue_hours, escalate=False):
        self.calls.append({"order_id": order_id, "assignee": assignee,
                           "escalate": escalate,
                           "hours": round(overdue_hours, 1)})
        return {"ok": True, "status": "sent"}


@pytest.fixture
def env():
    conn = get_conn(":memory:")
    init_db(conn)
    users = UserDAO(conn)
    safety = users.insert("zhangsan", "hashed", "safety")
    lisi = users.insert("lisi", "hashed", "responsible")
    svc = DispatchService(conn, rules=[{"scene": "hot_work", "assignee": "lisi"}])

    # 两张逾期单：5h（仅催办）与 30h（催办+越级）
    for created, deadline in (("2030-01-31 10:00:00", "2030-02-01 07:00:00"),
                              ("2030-01-31 11:00:00", "2030-01-31 06:00:00")):
        tid = TaskDAO(conn).insert(safety, "{}", "completed")
        RiskDAO(conn).insert(tid, "一般", "[]", "[]")
        WorkOrderDAO(conn).insert(task_id=tid, hazard_desc="逾期样本",
                                  clause=None, requirement="整改",
                                  risk_level="一般", worker_notice="")
        conn.execute("UPDATE work_orders SET deadline=?, created_at=? "
                     "WHERE task_id=?", (deadline, created, tid))
    conn.commit()
    return {"conn": conn, "svc": svc, "safety": safety, "lisi": lisi,
            "fake": FakeNotifier(), "audit": AuditDAO(conn)}


def test_scan_pushes_assignee_then_escalation(env):
    res = env["svc"].scan_overdue(as_of=FIXED_NOW, notifier=env["fake"])
    assert res["overdue"] == 2 and res["notified"] == 2
    assert res["escalated"] == 1                      # 仅 30h 那单
    by_escalate = [c for c in env["fake"].calls if c["escalate"]]
    assert len(by_escalate) == 1
    assert env["fake"].calls[0]["escalate"] is False  # 首推为责任人档
    assert res["push_sent"] == 3                      # 2 催办 + 1 越级


def test_scan_records_audit_actions(env):
    env["svc"].scan_overdue(as_of=FIXED_NOW, notifier=env["fake"])
    actions = [r["action"] for r in env["conn"].execute(
        "SELECT action FROM audit_logs").fetchall()]
    assert actions.count("overdue_notify") == 2
    assert actions.count("overdue_escalate") == 1


def test_scan_readonly_on_orders(env):
    before = env["conn"].execute(
        "SELECT COUNT(*) FROM work_orders").fetchone()[0]
    env["svc"].scan_overdue(as_of=FIXED_NOW, notifier=env["fake"])
    assert env["conn"].execute(
        "SELECT COUNT(*) FROM work_orders").fetchone()[0] == before
