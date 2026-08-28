"""系统自检门面（Phase 0）：page_diag 的 DB/全链路/模型检查下沉。"""
from __future__ import annotations

from services.db import scoped


def check_models() -> tuple[bool, str]:
    """模型注册表加载状态。"""
    from services.model_service import ModelService
    try:
        with scoped() as conn:
            ms = ModelService(conn)
            names = []
            for name in ("fire", "ppe"):
                m = ms.active_model(name)
                if m:
                    row = dict(m)
                    names.append(f"{name}={row.get('version') or row.get('name') or '已加载'}")
                else:
                    names.append(f"{name}=未注册")
        return True, "；".join(names)
    except Exception as exc:  # noqa: BLE001
        return False, f"加载异常：{exc}"[:120]


def check_db() -> tuple[bool, str]:
    """DB 读写 + 关键表计数。"""
    from dao.db import DEFAULT_DB_PATH
    try:
        with scoped() as conn:
            counts = {}
            for t in ("alarm_events", "notification_logs", "detection_records"):
                try:
                    counts[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                except Exception:  # noqa: BLE001 表缺失不阻断自检
                    counts[t] = "—"
        return True, f"{DEFAULT_DB_PATH} ｜ " + " ｜ ".join(
            f"{k}={v}" for k, v in counts.items())
    except Exception as exc:  # noqa: BLE001
        return False, f"DB 异常：{exc}"[:120]


def check_fulllink(notify_svc) -> tuple[bool, str]:
    """假告警 → 推送全链路（notify 未启用时自动 skipped 留痕）。"""
    from services.task_service import TaskService
    try:
        with scoped() as conn:
            ts = TaskService(conn)
            aid = ts.create_alarm_event(
                session_id="selftest", task_id=None, scene_id="hot_work",
                cls="spark", conf=0.99, source="自检", force=True)
            if not aid:
                return False, "创建告警事件失败"
            res = notify_svc.push_alarm(aid)
            if res.get("ok"):
                tag = "（模拟）" if notify_svc._demo_mode() else ""
                return True, f"告警 {aid} -> 推送 {res.get('status')}{tag}"
            return False, (f"告警 {aid} -> 推送 {res.get('status')} ｜ "
                           f"{res.get('error')}")
    except Exception as exc:  # noqa: BLE001
        return False, f"全链路异常：{exc}"[:120]


def check_video_source(source: str) -> dict:
    """单视频源连通性自检（转调 core.video_source）。"""
    from core.video_source import check_source
    return check_source(source)
