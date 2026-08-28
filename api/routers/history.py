"""检测历史分析路由（Phase 3 历史页数据源）：明细/按日聚合/类别分布/任务风险。

全部只读，业务查询复用 services.history_service；权限 admin+safety。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from api.deps import require_roles
from services import history_service

router = APIRouter(prefix="/history", tags=["history"])

_staff = require_roles("admin", "safety")


@router.get("/records")
def records(start: str | None = None, end: str | None = None,
            severity: str | None = None, cls: str | None = None,
            limit: int = Query(500, ge=1, le=2000),
            user=Depends(_staff)) -> list[dict]:
    """检测明细（帧/目标级，新→旧）。"""
    rows = history_service.query_records(start, end, severity=severity, cls=cls,
                                         limit=limit)
    return rows


@router.get("/stats-by-date")
def stats_by_date(start: str | None = None, end: str | None = None,
                  user=Depends(_staff)) -> list[dict]:
    """按日聚合：检测帧数与合规/警告/不合规分布（合规率趋势图）。"""
    return history_service.stats_by_date(start, end)


@router.get("/severity-breakdown")
def severity_breakdown(start: str | None = None, end: str | None = None,
                       user=Depends(_staff)) -> list[dict]:
    """类别命中分布（隐患 TOP 柱状图）。"""
    return history_service.severity_breakdown(start, end)


@router.get("/task-risks")
def task_risks(start: str | None = None, end: str | None = None,
               user=Depends(_staff)) -> list[dict]:
    """任务级风险一览（工单+风险+改判）。"""
    return history_service.task_risks(start, end)
