"""任务/研判路由：影像上传、文字建单、进度/结果轮询、证据链、改判、对话查询。

权限：上报/研判/改判类为 admin+safety（服务层 PermissionService 再按动作
强制）；进度/结果轮询任何已登录角色可用（属主隔离由服务层保证）。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from api.deps import CurrentUser, get_current_user, require_roles
from api.schemas import (ChatQueryIn, EnhanceIn, OverrideIn, RunIn,
                         TextHazardIn)
from api.uploads import UploadedLike
from services import lookup_service, order_service, task_entry

router = APIRouter(prefix="/tasks", tags=["tasks"])

# 静态路由先注册，避免被 /{task_id}/... 动态段吞掉
_capabilities = require_roles("admin", "safety")


@router.get("/capabilities")
def capabilities(user: CurrentUser = Depends(_capabilities)) -> dict:
    """前端能力开关：语音/AI 预填可用性 + 隐患键下拉选项（高危置顶）。"""
    from services.enhance_service import EnhanceEngine
    return {
        "asr_available": task_entry.asr_available(),
        "enhance_available": bool(EnhanceEngine().available()),
        "hazard_options": task_entry.hazard_options(),
    }


@router.post("/media")
def upload_media(
    file: UploadFile = File(...),
    scene_id: str = Form("hot_work"),
    permit_info: str = Form("{}"),
    auto_run: bool = Form(True),
    user: CurrentUser = Depends(_capabilities),
) -> dict:
    """影像上报（multipart）：魔数/大小校验 + 落盘 + 建任务，可选拉起后台研判。

    permit_info 为作业票字段 JSON 串（scene/fire_level/watcher/
    valid_until/area/extinguisher/fire_blanket/approval，见统一上报页）。
    """
    try:
        permit = json.loads(permit_info or "{}")
    except json.JSONDecodeError:
        raise HTTPException(status_code=400,
                            detail="permit_info 必须是 JSON 对象")
    if not isinstance(permit, dict):
        raise HTTPException(status_code=400,
                            detail="permit_info 必须是 JSON 对象")
    permit.setdefault("scene", scene_id)
    data = file.file.read()
    tid, media_rel, err = task_entry.create_media_task(
        user.user_id, permit, UploadedLike(file.filename, data),
        source="upload")
    if err:
        raise HTTPException(status_code=400, detail=err)
    started = False
    if auto_run:
        started = task_entry.start_async_run(
            tid, user.user_id, [media_rel] if media_rel else [], permit,
            scene_id=scene_id)
    return {"task_id": tid, "media_path": media_rel, "async_started": started}


@router.post("/text")
def create_text_hazard(body: TextHazardIn,
                       user: CurrentUser = Depends(_capabilities)) -> dict:
    """文字线索建单：跳过视觉链路，按 severity 查表定级，直接进派发闭环。"""
    res = task_entry.create_text_hazard(
        user.user_id, body.description, body.hazard_key,
        scene_id=body.scene_id, location=body.location)
    if not res.get("ok"):
        raise HTTPException(status_code=400,
                            detail=res.get("error", "创建失败"))
    return res


@router.post("/asr-transcribe")
def asr_transcribe(file: UploadFile = File(...),
                   user: CurrentUser = Depends(get_current_user)) -> dict:
    """语音转写（asr.* 未配置时 501，前端按 /capabilities 静默不渲染）。"""
    if not task_entry.asr_available():
        raise HTTPException(status_code=501, detail="语音转写未配置")
    text, err = task_entry.asr_transcribe(
        file.file.read(), file.filename or "record.wav")
    if text is None:
        raise HTTPException(status_code=502, detail=err or "转写失败")
    return {"text": text}


@router.post("/enhance-extract")
def enhance_extract(body: EnhanceIn,
                    user: CurrentUser = Depends(_capabilities)) -> dict:
    """AI 提取预填：自由文本 → 类别/场景/描述/位置草稿（人工确认后才建单）。"""
    from services.enhance_service import EnhanceEngine
    enh = EnhanceEngine()
    out = enh.extract_hazard(body.text)
    if not out:
        raise HTTPException(status_code=502,
                            detail=enh.last_error or "AI 提取暂不可用")
    return out


@router.post("/query-chat")
def query_chat(body: ChatQueryIn,
               user: CurrentUser = Depends(_capabilities)) -> dict:
    """对话式只读查询：路由 + 按动作执行只读取数（空文本=最新待办清单）。"""
    return lookup_service.chat_execute(body.text)


@router.get("")
def list_tasks(user: CurrentUser = Depends(_capabilities)) -> list[dict]:
    """任务台账列表（工单+风险+改判+来源，服务层只读查询）。"""
    return lookup_service.history_orders()


@router.post("/{task_id}/run")
def start_run(task_id: str, body: RunIn,
              user: CurrentUser = Depends(_capabilities)) -> dict:
    """对已建任务发起/重试后台多 Agent 研判（进行中重复启动返回 409）。"""
    started = task_entry.start_async_run(
        task_id, user.user_id, body.images, body.permit_info,
        scene_id=body.scene_id)
    if not started:
        raise HTTPException(status_code=409,
                            detail="任务已在研判中，或无权操作该任务")
    return {"task_id": task_id, "async_started": True}


@router.get("/{task_id}/progress")
def task_progress(task_id: str,
                  user: CurrentUser = Depends(get_current_user)) -> dict:
    """研判进度 {agent: {status, cost_ms}}（非属主视角返回空）。"""
    return task_entry.progress(task_id, user.user_id)


@router.get("/{task_id}/result")
def task_result(task_id: str,
                user: CurrentUser = Depends(get_current_user)) -> dict:
    """取走异步研判结果（取后即清；未就绪/不存在 404，前端轮询用）。"""
    res = task_entry.async_result(task_id, user.user_id)
    if res is None:
        raise HTTPException(status_code=404, detail="结果未就绪或不存在")
    return res


@router.get("/{task_id}/agents")
def task_agents(task_id: str,
                user: CurrentUser = Depends(_capabilities)) -> list[dict]:
    """任务级 Agent 运行轨迹（多 Agent 研判页分步展示数据源）。"""
    return task_entry.agent_runs(task_id)


@router.get("/{task_id}/detail")
def task_detail(task_id: str,
                user: CurrentUser = Depends(_capabilities)) -> dict:
    """任务详情：概览行 + 检测/合规明细（404=任务不存在）。"""
    detail = lookup_service.task_detection_detail(task_id)
    if not detail.get("task"):
        raise HTTPException(status_code=404, detail="任务不存在")
    return detail


@router.post("/{task_id}/override")
def override(task_id: str, body: OverrideIn,
             user: CurrentUser = Depends(_capabilities)) -> dict:
    """人工改判风险等级（+ 纠偏样本落库 + 审计）。"""
    ok, msg = order_service.submit_override(task_id, user.user_id,
                                            body.new_level, body.reason)
    if not ok:
        raise HTTPException(status_code=404, detail=msg)
    return {"ok": True, "message": msg}
