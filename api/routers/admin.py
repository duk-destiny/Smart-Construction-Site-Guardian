"""管理端路由（全部 admin-only）：用户/模型/知识库/推送/自检/审计/反馈/数据清理。

细粒度动作权限（manage_users 等）仍由服务层 AuthService 强制——router 角色门
只做第一道 coarse 拦截。模型切换后与 ui.page_admin 同口径热加载运行中引擎。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response

from api.deps import require_roles
from api.schemas import (ClearDataIn, FeedbackReviewIn, ResetPasswordIn,
                         SwitchModelIn, UserCreateIn, UserDisabledIn)
from api.uploads import UploadedLike
from core.logging import get_logger  # 白名单（情况1）：日志
from services import admin_console, diag_service, realtime_entry

log = get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

_admin = require_roles("admin")


# ---------- 用户治理 ----------

@router.get("/users")
def list_users(user=Depends(_admin)) -> list[dict]:
    """用户列表（剔除 pwd_hash 字段，哈希不出 API）。"""
    rows = admin_console.list_users()
    return [{k: v for k, v in r.items() if k != "pwd_hash"} for r in rows]


@router.post("/users")
def create_user(body: UserCreateIn, user=Depends(_admin)) -> dict:
    """建用户（默认强制首登改密；动作权限 manage_users 在服务层强制）。"""
    res = admin_console.create_user(user.user_id, body.username,
                                    body.password, body.role,
                                    must_change_password=body.must_change_password)
    if not res.get("ok"):
        raise HTTPException(status_code=400,
                            detail=res.get("error", "创建失败"))
    return res


@router.post("/users/{target_user_id}/reset-password")
def reset_password(target_user_id: str, body: ResetPasswordIn,
                   user=Depends(_admin)) -> dict:
    """管理员重置密码（重置后强制对方下次登录改密）。"""
    res = admin_console.admin_reset_password(user.user_id, target_user_id,
                                             body.new_password)
    if not res.get("ok"):
        raise HTTPException(status_code=400,
                            detail=res.get("error", "重置失败"))
    return {"ok": True}


@router.post("/users/{target_user_id}/disabled")
def set_disabled(target_user_id: str, body: UserDisabledIn,
                 user=Depends(_admin)) -> dict:
    """停用/启用账号（守卫：不能停用自己/最后一名管理员，服务层校验）。"""
    res = admin_console.set_user_disabled(user.user_id, target_user_id,
                                          body.disabled)
    if not res.get("ok"):
        raise HTTPException(status_code=400,
                            detail=res.get("error", "操作失败"))
    return {"ok": True}


# ---------- 模型注册/切换 ----------

def _reload_running_engines() -> None:
    """模型切换后热加载（与 ui.page_admin._reload_running_engines 同口径）。

    让实时引擎与后台监控重建引擎列表拾取新 active 模型；失败留痕不阻断
    （切换本身已落 DB，下次引擎构建时自然拾取）。
    """
    try:
        realtime_entry.get_engine().reload()
    except Exception as exc:  # noqa: BLE001 热加载失败不阻断切换
        log.warning(f"实时引擎热加载失败: {exc}")
    try:
        from services.monitor_service import get_monitor
        mon = get_monitor()
        if mon is not None and getattr(mon, "engine", None) is not None:
            mon.engine.reload()
    except Exception as exc:  # noqa: BLE001
        log.warning(f"后台监控引擎热加载失败: {exc}")


@router.get("/models")
def list_models(user=Depends(_admin)) -> dict:
    """模型注册表（分族列表 + fire/ppe 当前活跃版本）。"""
    return {
        "models": admin_console.model_families(),
        "active": {"fire": admin_console.active_model("fire"),
                   "ppe": admin_console.active_model("ppe")},
    }


@router.post("/models/switch")
def switch_model(body: SwitchModelIn, user=Depends(_admin)) -> dict:
    """切换活跃模型版本（唯一事实源=model_registry.active，不回写 config）。"""
    admin_console.switch_model(body.name, body.model_id, user.user_id)
    active = admin_console.active_model(body.name)
    if active is None or active["id"] != body.model_id:
        raise HTTPException(status_code=400, detail="切换未生效：模型 ID 不存在")
    _reload_running_engines()
    return {"ok": True, "active": dict(active)}


# ---------- 知识库 ----------

@router.get("/kb/docs")
def kb_docs(user=Depends(_admin)) -> list[dict]:
    """已导入规范文档列表。"""
    return admin_console.kb_docs()


@router.post("/kb/import")
def kb_import(file: UploadFile = File(...), user=Depends(_admin)) -> dict:
    """规范 PDF 导入（魔数/大小校验 → 解析 → 向量化入库，BGE 子进程）。"""
    res = admin_console.import_pdf(
        user.user_id, UploadedLike(file.filename, file.file.read()))
    if not res.get("ok"):
        raise HTTPException(status_code=400,
                            detail=res.get("error", "导入失败"))
    return res


# ---------- 推送通道 ----------

@router.get("/notify/status")
def notify_status(user=Depends(_admin)) -> dict:
    """推送通道状态（不回显 webhook URL，防密钥经 API 泄露）。"""
    return admin_console.notify_status()


@router.post("/notify/test")
def notify_test(user=Depends(_admin)) -> dict:
    """测试推送（demo_mode 时捕获到 mock_capture.jsonl，返回发送结果）。"""
    return admin_console.notify_test_push()


@router.get("/mock-capture")
def mock_capture(n: int = Query(10, ge=1, le=100),
                 user=Depends(_admin)) -> list[dict]:
    """演示模式捕获的最近 n 条推送 payload。"""
    return admin_console.mock_capture_tail(n)


# ---------- 系统自检 ----------

@router.post("/self-check")
def self_check(user=Depends(_admin)) -> dict:
    """系统自检：模型注册表 / DB / 告警→推送全链路（notify 未启用自动 skipped）。"""
    from services.notify_service import NotificationService
    items = []
    for name, fn, arg in (
        ("模型注册表", diag_service.check_models, None),
        ("数据库", diag_service.check_db, None),
        ("告警→推送全链路", diag_service.check_fulllink, NotificationService()),
    ):
        ok, msg = fn(arg) if arg is not None else fn()
        items.append({"item": name, "ok": ok, "message": msg})
    return {"ok": all(i["ok"] for i in items), "items": items}


@router.get("/video-source/check")
def video_source_check(source: str, user=Depends(_admin)) -> dict:
    """单视频源连通性自检（admin-only；source 含凭据时响应打码在 core 层）。"""
    return realtime_entry.check_source(source)


# ---------- 台账 / 审计 / 反馈 / 数据清理 ----------

@router.get("/hazard-summary")
def hazard_summary(limit: int = Query(100, ge=1, le=500),
                   user=Depends(_admin)) -> list[dict]:
    return admin_console.hazard_summary_rows(limit)


@router.get("/audit")
def audit_rows(limit: int = Query(200, ge=1, le=1000),
               user=Depends(_admin)) -> list[dict]:
    return admin_console.audit_rows(limit)


@router.get("/audit/export")
def audit_export(user=Depends(_admin)) -> Response:
    text, rows = admin_console.audit_csv()
    return Response(
        content=text, media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition":
                 f"attachment; filename=audit_{rows}.csv"})


@router.get("/feedback")
def feedback(limit: int = Query(500, ge=1, le=2000),
             user=Depends(_admin)) -> list[dict]:
    return admin_console.feedback_samples(limit)


@router.post("/feedback/{feedback_id}/review")
def feedback_review(feedback_id: str, body: FeedbackReviewIn,
                    user=Depends(_admin)) -> dict:
    admin_console.review_feedback(feedback_id, body.status, user.user_id)
    return {"ok": True}


@router.get("/feedback/export")
def feedback_export(user=Depends(_admin)) -> Response:
    return Response(
        content=admin_console.feedback_csv_text(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=feedback.csv"})


@router.get("/notification-logs")
def notification_logs(limit: int = Query(200, ge=1, le=1000),
                      user=Depends(_admin)) -> list[dict]:
    return admin_console.notification_logs(limit)


@router.get("/eval")
def eval_summary(user=Depends(_admin)) -> list[dict]:
    """模型逐类评测摘要（data/eval/model_eval.json 解析；缺失返回空表）。"""
    return admin_console.eval_summary_rows()


@router.post("/data/clear")
def data_clear(body: ClearDataIn, user=Depends(_admin)) -> dict:
    """清空全部业务数据（确认码必须为 RESET；保留账号/审计/知识库/模型注册）。"""
    return admin_console.clear_all_data(user.user_id, body.confirmation)
