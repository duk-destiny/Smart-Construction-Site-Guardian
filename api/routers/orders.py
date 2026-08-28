"""工单路由：台账/我的整改单/待验收/派发/整改提交/验收/逾期/导出。

权限：台账/派发/验收为 admin+safety（服务层 override 动作再强制）；
我的整改单与整改提交任何已登录角色可用（仅本单责任人可提交，
服务层强制）。整改进而按手机浏览器可用设计（Phase 3 前端响应式）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from api.deps import CurrentUser, get_current_user, require_roles
from api.schemas import DispatchIn, ReviewIn
from api.uploads import UploadedLike
from services import lookup_service, order_service

router = APIRouter(prefix="/orders", tags=["orders"])

_staff = require_roles("admin", "safety")


def _utc_now_str() -> str:
    """与 services.dispatch_service._now_str 同口径的 UTC 时间串。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


@router.get("")
def all_orders(user=Depends(_staff)) -> list[dict]:
    """全部工单台账（工单+风险+改判+来源）。"""
    return lookup_service.history_orders()


@router.get("/mine")
def my_orders(user: CurrentUser = Depends(get_current_user)) -> list[dict]:
    """我的整改单（责任人首页数据源；status: open/rejected 可提交）。"""
    return order_service.my_orders(user.user_id)


@router.get("/pending-review")
def pending_review(user=Depends(_staff)) -> list[dict]:
    """待验收队列（含责任人用户名与整改照片存在性标注）。"""
    return order_service.pending_review_orders()


@router.get("/overdue")
def overdue(as_of: str | None = None,
            user=Depends(_staff)) -> list[dict]:
    """逾期未销项工单列表（as_of 缺省取当前 UTC，可传时间游标）。"""
    return lookup_service.overdue_rows(as_of or _utc_now_str())


@router.get("/by-task/{task_id}/panel")
def dispatch_panel(task_id: str, scene_id: str = "hot_work",
                   user=Depends(_staff)) -> dict:
    """派发面板：工单行+责任人名+responsible 候选+规则建议+默认时限。"""
    panel = order_service.dispatch_panel(task_id, scene_id=scene_id)
    if panel is None:
        raise HTTPException(status_code=404, detail="该任务尚未生成工单")
    return panel


@router.post("/by-task/{task_id}/dispatch")
def dispatch(task_id: str, body: DispatchIn,
             user=Depends(_staff)) -> dict:
    """派发/改派工单（服务层鉴权+审计+派发即推送责任人）。"""
    ok, msg = order_service.dispatch_order(
        task_id, user.user_id, body.assignee, body.hours,
        scene_id=body.scene_id or "hot_work")
    if not ok:
        status = 403 if msg.startswith("权限不足") else 400
        raise HTTPException(status_code=status, detail=msg)
    return {"ok": True, "message": msg}


@router.post("/{order_id}/rectification")
def submit_rectification(order_id: str, note: str = Form(...),
                         photos: list[UploadFile] = File(default=[]),
                         user: CurrentUser = Depends(get_current_user)) -> dict:
    """责任人提交整改：说明 + 现场照片（multipart，消毒落盘在服务层）。"""
    ups = [UploadedLike(f.filename, f.file.read()) for f in photos or []]
    ok, msg = order_service.submit_rectification(order_id, user.user_id,
                                                 note, ups)
    if not ok:
        status = 403 if msg.startswith("权限不足") else 400
        raise HTTPException(status_code=status, detail=msg)
    return {"ok": True, "message": msg}


@router.post("/{order_id}/review")
def review(order_id: str, body: ReviewIn,
           user=Depends(_staff)) -> dict:
    """验收：通过销项 / 驳回退回（驳回必须填原因，服务层校验）。"""
    ok, msg = order_service.review_order(order_id, user.user_id,
                                         body.approve, body.reason)
    if not ok:
        status = 403 if msg.startswith("权限不足") else 400
        raise HTTPException(status_code=status, detail=msg)
    return {"ok": True, "message": msg}


@router.post("/{order_id}/export")
def export(order_id: str, user=Depends(_staff)) -> dict:
    """导出工单台账 Excel，返回文件名与下载地址（下载走 /api/reports/exports）。"""
    ok, result = order_service.export_order_excel(order_id, user.user_id)
    if not ok:
        raise HTTPException(status_code=400, detail=result)
    import os
    name = os.path.basename(result)
    return {"ok": True,
            "file": {"name": name,
                     "download_url": f"/api/reports/exports/{quote(name)}"}}
