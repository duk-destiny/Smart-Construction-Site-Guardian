"""检测历史门面（Phase 0）：历史分析页与实时持久化的入口。

原实现在 UI 层以 @st.cache_data 包裹 get_conn + 内联 SQL JOIN——
SQL 下沉到本模块（dao 仍是唯一 SQL 层），UI 保留缓存装饰器只缓存
本模块返回的 dict 列表。
"""
from __future__ import annotations

from services.db import scoped
from services.task_service import TaskService


def query_records(start: str | None = None, end: str | None = None,
                  severity: str | None = None,
                  cls: str | None = None) -> list[dict]:
    """检测明细查询（帧/目标级）。"""
    from dao.models import DetectionRecordDAO
    with scoped() as conn:
        rows = DetectionRecordDAO(conn).query(start, end, severity=severity,
                                              cls=cls)
        return [dict(r) for r in rows]


def stats_by_date(start: str | None = None,
                  end: str | None = None) -> list[dict]:
    """按日聚合：合规率趋势。"""
    from dao.models import DetectionRecordDAO
    with scoped() as conn:
        rows = DetectionRecordDAO(conn).stats_by_date(start, end)
        return [dict(r) for r in rows]


def severity_breakdown(start: str | None = None,
                       end: str | None = None) -> list[dict]:
    """类别命中分布。"""
    from dao.models import DetectionRecordDAO
    with scoped() as conn:
        rows = DetectionRecordDAO(conn).severity_breakdown(start, end)
        return [dict(r) for r in rows]


def task_risks(start: str | None = None,
               end: str | None = None) -> list[dict]:
    """任务级风险一览（工单 + 风险 + 改判；原 UI 内联 JOIN 下沉）。"""
    with scoped() as conn:
        sql = """
            SELECT w.task_id, w.hazard_desc, w.risk_level AS wo_risk_level,
                   r.risk_level AS auto_level,
                   r.override_level, r.override_reason,
                   r.reasons_json,
                   w.created_at
            FROM work_orders w
            LEFT JOIN risks r ON r.task_id = w.task_id
            WHERE 1=1
        """
        params: list = []
        if start:
            sql += " AND w.created_at >= ?"
            params.append(start)
        if end:
            sql += " AND w.created_at <= ?"
            params.append(end + " 23:59:59")
        sql += " ORDER BY w.created_at DESC"
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def record_frame(session_id: str, frame_status: str, dets: list[dict],
                 mode: str = "realtime") -> None:
    """实时链路单帧持久化（原 page_realtime._persist 下沉；失败留痕不中断）。"""
    from dao.models import DetectionRecordDAO
    from core.compliance import SEVERITY
    from core.logging import get_logger
    log = get_logger(__name__)
    try:
        with scoped() as conn:
            rows = [{
                "scene_id": d.get("scene"),
                "cls": d.get("cls"),
                "conf": d.get("conf", 0.0),
                "severity": SEVERITY.get(d.get("cls"), "warning"),
                "track_id": d.get("track_id"),
                "track_frames": d.get("track_frames"),
            } for d in dets]
            DetectionRecordDAO(conn).bulk_insert(session_id, frame_status,
                                                 rows, mode=mode)
    except Exception as exc:  # noqa: BLE001 历史写入失败不应中断监测
        log.warning(f"历史持久化失败: {exc}")


def raise_realtime_alarm(session_id: str, scene_id: str | None,
                         cls: str | None, conf: float | None,
                         source: str | None = None,
                         annotated_bgr=None, force: bool = False) -> str | None:
    """实时告警完整链路（建告警→证据→回填→推送→条款挂载）。"""
    with scoped() as conn:
        return TaskService(conn).raise_alarm(
            session_id=session_id, scene_id=scene_id, cls=cls, conf=conf,
            source=source, annotated_bgr=annotated_bgr, force=force)


def raise_critical_alarm(session_id: str, dets: list[dict],
                         source: str | None = None,
                         annotated_bgr=None) -> str | None:
    """从一帧检测结果中选取最高危项触发告警（Phase 0 分层收口）。

    「哪些检测项算 critical」是合规业务判定（severity 查表），收口在服务层；
    UI 只递检测列表，不再 import core.compliance。场景取自选中检测项自身
    （引擎打标 d["scene"]）。无检测项返回 None。
    """
    from core.compliance import SEVERITY
    if not dets:
        return None
    crit = [d for d in dets
            if SEVERITY.get(d.get("cls"), "warning") == "critical"] or [dets[0]]
    d = crit[0]
    return raise_realtime_alarm(
        session_id=session_id, scene_id=d.get("scene"),
        cls=d.get("cls"), conf=d.get("conf"),
        source=source, annotated_bgr=annotated_bgr)
