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


def chat_execute(text: str) -> dict:
    """对话式只读查询（API 用）：路由 + 按动作执行只读取数，一次返回。

    与 ui/page_upload Tab③ 的消费语义逐条对齐（detail 卡/清单/逾期/周统计）；
    空文本返回最新待办清单。绝不产生写操作（读写硬隔离）。
    """
    import dataclasses
    from datetime import date, timedelta

    from services.intent_router import IntentRouter
    from services.report_service import WeeklyReportService

    with scoped() as conn:
        router = IntentRouter(conn)
        if not (text or "").strip():
            # 空文本 = 最新待办清单（与 Streamlit 版空态一致）
            rows = router.list_view(limit=8)
            return {"action": "order_list", "tier": "rule", "status": None,
                    "days": 7, "order_id": None, "hint": "最新待办工单",
                    "candidates": [r["id"] for r in rows], "data": rows}
        route = router.route(text)
        data = None
        action = route.action
        if action == "order_detail" and route.order_id:
            data = router.detail_view(route.order_id)
        elif action == "order_list":
            statuses = (route.status,) if route.status else                 ("open", "rejected", "submitted")
            data = router.list_view(statuses=statuses, limit=8)
        elif action == "confirm_list":
            data = [router.detail_view(cid) or {"id": cid}
                    for cid in route.candidates[:8]]
        elif action == "overdue_stats":
            from services.dispatch_service import _now_str
            data = {"rows": router.overdue_rows(_now_str())}
        elif action == "weekly_stats":
            end = date.today().isoformat()
            start = (date.today() - timedelta(days=route.days - 1)).isoformat()
            data = WeeklyReportService(conn).gather(start, end)
    out = dataclasses.asdict(route)
    out["data"] = data
    return out


def task_detection_detail(task_id: str) -> dict:
    """单任务检测/合规明细（历史列表「查看检测数据」+ API 任务详情）。

    Phase 2 起附带 task/risk 概览行（向后兼容的增量键，UI 原调用不受影响）；
    任务不存在时 task 为 None，由调用方判空。
    """
    with scoped() as conn:
        task = conn.execute(
            "SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        risk = conn.execute(
            "SELECT * FROM risks WHERE task_id=?", (task_id,)).fetchone()
        detections = conn.execute(
            "SELECT * FROM detections WHERE task_id=?", (task_id,)).fetchall()
        comps = conn.execute(
            "SELECT * FROM compliances WHERE task_id=?", (task_id,)).fetchall()
    return {
        "task": dict(task) if task else None,
        "risk": dict(risk) if risk else None,
        "detections": [dict(d) for d in detections],
        "compliances": [dict(c) for c in comps],
    }
