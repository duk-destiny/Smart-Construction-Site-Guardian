"""工单查询/对话速查门面（Phase 0）：统一上报 Tab③ 与历史列表的只读入口。"""
from __future__ import annotations

from services.db import scoped


def route(text: str):
    """意图路由（只读）：返回 RouteResult。"""
    from services.intent_router import IntentRouter
    with scoped() as conn:
        return IntentRouter(conn).route(text)


def detail_view(order_id: str) -> dict | None:
    from services.intent_router import IntentRouter
    with scoped() as conn:
        card = IntentRouter(conn).detail_view(order_id)
    return dict(card) if card is not None else None


def list_view() -> list[dict]:
    from services.intent_router import IntentRouter
    with scoped() as conn:
        return [dict(r) for r in IntentRouter(conn).list_view()]


def overdue_rows(as_of: str) -> list[dict]:
    from services.intent_router import IntentRouter
    with scoped() as conn:
        return [dict(r) for r in IntentRouter(conn).overdue_rows(as_of)]


def weekly_stats(start: str, end: str) -> dict:
    """周报口径的只读统计（速查 Tab「本周统计」）。"""
    from services.report_service import WeeklyReportService
    with scoped() as conn:
        return WeeklyReportService(conn).gather(start, end)


def history_orders() -> list[dict]:
    """历史研判列表（工单+风险+改判+来源）。"""
    from dao.models import WorkOrderDAO
    with scoped() as conn:
        return [dict(r) for r in WorkOrderDAO(conn).list_all_with_risk()]


def task_detection_detail(task_id: str) -> dict:
    """单任务检测/合规明细（历史列表「查看检测数据」）。"""
    with scoped() as conn:
        detections = conn.execute(
            "SELECT * FROM detections WHERE task_id=?", (task_id,)).fetchall()
        comps = conn.execute(
            "SELECT * FROM compliances WHERE task_id=?", (task_id,)).fetchall()
    return {
        "detections": [dict(d) for d in detections],
        "compliances": [dict(c) for c in comps],
    }
