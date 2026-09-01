"""上报/研判门面（Phase 0）：统一上报页与多 Agent 研判页的数据入口。

UI 只调本模块函数；上传文件的消毒/魔数/大小校验、媒体落盘（BASE_DIR 锚点）
在此收口——原实现散在 UI 层且仅凭扩展名白名单（Phase 1 修复项）。
"""
from __future__ import annotations

import os

from core.config import shared_config
from core.evidence import sanitize_filename
from core.logging import get_logger
from core.paths import data_path, to_rel
from core.upload_guard import check_upload
from services.db import scoped
from services.task_service import TaskService

log = get_logger(__name__)

# 上传大小上限（MB）默认值，可被 config.upload.* 覆盖（Phase 1 配置化）
_DEFAULT_LIMITS = {"max_image_mb": 20, "max_video_mb": 200, "max_pdf_mb": 20}


def upload_limits() -> dict:
    conf = shared_config().get("upload") or {}
    out = dict(_DEFAULT_LIMITS)
    if isinstance(conf, dict):
        for k in out:
            if conf.get(k) is not None:
                out[k] = int(conf[k])
    return out


def save_media(user_id: str | None, uploaded) -> tuple[str | None, str]:
    """保存上传的图片/视频：消毒文件名 → 魔数/大小校验 → 落盘。

    返回 (相对路径, 错误消息)；校验失败时路径为 None 且不改盘。
    抛出的权限/校验异常由调用方按 ValueError/PermissionError 呈现。
    """
    data = uploaded.getvalue()
    ok, err = check_upload(data, uploaded.name, upload_limits())
    if not ok:
        return None, err
    save_dir = data_path("uploads")
    os.makedirs(save_dir, exist_ok=True)
    import uuid
    name = sanitize_filename(uploaded.name, fallback="media")
    prefix = uuid.uuid4().hex[:8]
    safe_name = f"{prefix}_{name}"
    path = os.path.join(save_dir, safe_name)
    with open(path, "wb") as f:
        f.write(data)
    return to_rel(path), ""


def create_media_task(user_id: str | None, permit_info: dict,
                      uploaded, source: str = "upload") -> tuple[str, str | None, str]:
    """创建影像任务并保存媒体。返回 (task_id, media_rel_path, error)。"""
    media_rel: str | None = None
    if uploaded is not None:
        media_rel, err = save_media(user_id, uploaded)
        if media_rel is None:
            return "", None, err
    with scoped() as conn:
        svc = TaskService(conn)
        tid = svc.create_task(user_id, [], permit_info, source=source)
    return tid, media_rel, ""


def create_text_hazard(user_id: str | None, description: str, hazard_key: str,
                       scene_id: str = "hot_work",
                       location: str | None = None) -> dict:
    """文字线索建单，返回 {ok, task_id, risk_level, work_order, error}。"""
    try:
        with scoped() as conn:
            svc = TaskService(conn)
            tid = svc.create_text_hazard(user_id, description, hazard_key,
                                         scene_id=scene_id, location=location)
            risk_row = svc.risks.get_by_task(tid)
            wo = svc.work_orders.get_by_task(tid)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "task_id": tid,
        "risk_level": risk_row["risk_level"] if risk_row else "一般",
        "work_order": {
            "risk_level": risk_row["risk_level"] if risk_row else "一般",
            "hazard_desc": wo["hazard_desc"],
            "clause": wo["clause"],
            "requirement": wo["requirement"],
        } if wo else {},
        "worker_notice": wo["worker_notice"] if wo else "",
        "error": "",
    }


def start_async_run(task_id: str, user_id: str | None, images: list[str],
                    permit_info: dict, scene_id: str = "hot_work") -> bool:
    with scoped() as conn:
        return TaskService(conn).start_async_run(
            task_id, user_id, images, permit_info, scene_id=scene_id)


def async_result(task_id: str, user_id: str | None) -> dict | None:
    with scoped() as conn:
        return TaskService(conn).pop_async_result(task_id, user_id)


def progress(task_id: str, user_id: str | None) -> dict:
    with scoped() as conn:
        return TaskService(conn).get_progress(task_id, user_id)


def run_sync(task_id: str, user_id: str | None, images: list[str],
             permit_info: dict, scene_id: str = "hot_work") -> dict:
    """同步研判（兼容模式）：Orchestrator 构造/执行/落库/润色/审计全在服务层。"""
    from pipeline.orchestrator import Orchestrator
    with scoped() as conn:
        svc = TaskService(conn)
        orch = Orchestrator(progress_cb=svc.update_progress, scene_id=scene_id)
        result = orch.execute(task_id, images=images, permit_info=permit_info)
        svc.save_result(task_id, result.payload)
        wo = result.payload.get("work_order") or {}
        if getattr(orch, "action", None) is not None:
            orch.action.polish(task_id, wo.get("hazard_desc", ""),
                               wo.get("clause", ""),
                               wo.get("requirement", ""),
                               wo.get("deadline", ""))
        from services.audit_service import AuditService
        AuditService(conn).append(user_id, "execute", {"task_id": task_id})
        return result.to_dict()


def agent_runs(task_id: str) -> list[dict]:
    """任务级 Agent 运行轨迹（证据链展示）。"""
    with scoped() as conn:
        return [dict(r) for r in TaskService(conn).list_agent_runs(task_id)]


def evaluate_compliance(detections: list[dict]) -> dict:
    """三级合规研判（core.compliance.evaluate 转发）。

    「合规/警告/不合规」判定属 core 业务计算（severity 查表），按分层
    纪律由服务层转发，UI 不直接 import core——页面拿到结果后仅作横幅
    渲染（ui.components.compliance_banner 为纯视图转换）。
    """
    from core.compliance import evaluate
    return evaluate(detections or [])


def hazard_options() -> list[dict]:
    """隐患键下拉选项（API/前端用）：key + 中文名 + severity，高危置顶。

    「哪些键可选、怎么排序」是合规业务常量（severity 查表 + 白名单中文名），
    按白名单裁定口径由服务层转发；safe 正向信号不构成上报项，不下发。
    """
    from core.compliance import SEVERITY
    from core.yolo_engine import WHITELIST_CN
    items = [{"key": k, "label": WHITELIST_CN.get(k, k), "severity": v}
             for k, v in SEVERITY.items()
             if k != "none" and v in ("critical", "warning")]
    items.sort(key=lambda it: (0 if it["severity"] == "critical" else 1,
                               it["label"]))
    return items


# ---------- 语音转写（可选增强）----------

def asr_available() -> bool:
    """语音入口是否可用（未配置 asr.* 时为 False → UI 静默不渲染）。"""
    from core.asr_engine import AsrEngine
    return AsrEngine().available()


def asr_transcribe(audio: bytes, name: str) -> tuple[str | None, str | None]:
    """转写音频，返回 (文本, 错误)；失败文本为 None、错误留 last_error。"""
    from core.asr_engine import AsrEngine
    eng = AsrEngine()
    text = eng.transcribe(audio, name or "record.wav")
    return text, eng.last_error
