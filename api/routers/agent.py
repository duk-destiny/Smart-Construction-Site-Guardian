"""认知层 /agent/* 端点（设计文档 §5.12/§5.13，M2；T5 接入双层路由）。

鉴权（§5.13）：全部挂 `Depends(get_current_user)`，未认证 401；
属主校验：run/session 跨属主一律 404（不泄露存在性）。

响应策略（§5.11 双层，T5 实装）：`_route_path` 按 IntentRouter 规则层
判定——有把握命中封闭查询（单张详情/消歧/非空列表/逾期/统计）→
'fast'，同步直返旧 ChatRoute 结构（零契约变化）；认知关键词命中或
规则无把握 → 'cognitive'，建会话/消息 + 异步 run 返 run_id。

v2.2 对话窗口增补：会话 CRUD（列表/新建/改名/归档/删除）、对话附件
上传与服务端强制绑定、模型能力信息（前端能力弹窗数据源）、TTS 合成。
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response

from api.deps import CurrentUser, get_current_user
from api.schemas import (AgentChatIn, AgentConfirmIn, SessionCreateIn,
                         SessionPatchIn)
from services.agent.run_service import PlanRunBusy, PlanRunService

router = APIRouter(prefix="/agent", tags=["agent"])

# 对话附件白名单（图像/视频，与 media 下发白名单子集对齐）
_ATTACHMENT_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".webp",
                    ".mp4", ".mov", ".avi", ".webm", ".mkv")


def _route_path_of(route) -> str:
    """按 RouteResult 判快/慢（§5.11 双层响应策略）。

    'fast'：规则层有把握的封闭查询（具体工单详情/非空列表/消歧/
    逾期/统计）→ 同步直返旧结构；'cognitive'：认知关键词命中，
    或规则无把握（空列表/unknown 人工档）→ 交认知内核规划。
    """
    if route.path == "cognitive":
        return "cognitive"
    if route.action == "order_detail" and route.order_id:
        return "fast"
    if route.action in ("overdue_stats", "weekly_stats"):
        return "fast"
    if route.action in ("order_list", "confirm_list") and route.candidates:
        return "fast"
    return "cognitive"


def _route_path(text: str) -> str:
    """快/慢分流钩子（§5.11）：空文本守旧契约（最新待办清单）走快路径。"""
    if not (text or "").strip():
        return "fast"
    from services import lookup_service
    return _route_path_of(lookup_service.route(text))


def _own_session(dao, session_id: str, user_id: str):
    """取会话并校验属主（跨属主/不存在一律 404，不泄露存在性）。"""
    sess = dao.get_session(session_id)
    if sess is None or sess["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="会话不存在")
    return sess


def _validated_attachments(paths: list[str]) -> list[str]:
    """校验对话附件路径：必须由 /agent/uploads 签发（目录锚定+白名单）。

    任何越界（绝对路径/..穿越/目录不符/不存在/扩展名不符）直接 400——
    附件是后续服务端强制绑定给 run_video_pipeline 的唯一合法来源。
    """
    from core.paths import data_path, to_rel
    base = data_path("uploads", "chat")
    out: list[str] = []
    for raw in paths or []:
        rel = (raw or "").strip().replace("\\", "/")
        if not rel:
            continue
        if rel.startswith("/") or ".." in rel.split("/"):
            raise HTTPException(status_code=400, detail=f"附件路径非法: {raw}")
        full = os.path.join(str(base), os.path.basename(rel))
        if not os.path.isfile(full):
            raise HTTPException(status_code=400, detail=f"附件不存在: {raw}")
        if not rel.lower().endswith(_ATTACHMENT_EXTS):
            raise HTTPException(status_code=400,
                                detail=f"附件扩展名不支持: {raw}")
        out.append(to_rel(full))
    return out


def _start_cognitive(user_id: str, text: str,
                     session_id: str | None = None,
                     intent: str | None = None,
                     attachments: list[str] | None = None) -> dict:
    """认知路径启动：建会话/消息 + 创建异步 run（/agent/chat 与旧端点薄壳共用）。"""
    from dao.models import AgentChatDAO
    from services.db import scoped
    import json as _json
    with scoped() as conn:
        dao = AgentChatDAO(conn)
        if session_id:
            _own_session(dao, session_id, user_id)
        else:
            session_id = dao.create_session(user_id,
                                            title=(text or "")[:24])
    try:
        run_id = PlanRunService.create_run(user_id, session_id, text,
                                           intent=intent,
                                           attachments=attachments or None)
    except PlanRunBusy:
        # 准入背压：同步返 busy，前端稍后重试（不建会话消息避免孤儿轮）
        return {"path": "cognitive", "status": "busy",
                "session_id": session_id}
    with scoped() as conn:
        AgentChatDAO(conn).insert_message(
            session_id, "user", text, run_id=run_id,
            attachments_json=(_json.dumps(attachments, ensure_ascii=False)
                              if attachments else None))
    return {"path": "cognitive", "run_id": run_id, "session_id": session_id,
            "status": "pending"}


def dispatch_chat(user_id: str, text: str,
                  session_id: str | None = None,
                  attachments: list[str] | None = None) -> dict:
    """双层分流执行体（§5.11）：快 → chat_execute 同步旧结构；
    慢 → 建会话 + 异步 run。/agent/chat 与 /tasks/query-chat 薄壳共用。

    携带附件时强制走认知路径（附件注入仅在认知 run 生效）；
    问候/寒暄走规则快路径零 LLM 直答（v2.2 闲聊归宿）。
    """
    from services import lookup_service
    if not (attachments or []):
        if not (text or "").strip():
            return lookup_service.chat_execute(text or "")   # 空文本=最新待办清单
        if lookup_service.greeting_re().match(text.strip()):
            return lookup_service.greeting_reply()
        route = lookup_service.route(text)
        if _route_path_of(route) == "fast":
            return lookup_service.chat_execute(text)
        return _start_cognitive(user_id, text, session_id, intent=route.action)
    return _start_cognitive(user_id, text, session_id,
                            attachments=attachments)


@router.post("/chat")
def agent_chat(body: AgentChatIn,
               user: CurrentUser = Depends(get_current_user)) -> dict:
    """对话入口（§5.11 双层）：快路径同步直返旧查询结构；认知路径建会话/
    消息 + 创建认知 run（后台执行），返回 run_id。attachments 非空强制
    认知路径（服务端强制绑定给 run_video_pipeline）。"""
    atts = _validated_attachments(body.attachments)
    return dispatch_chat(user.user_id, body.text, body.session_id, atts)


@router.get("/runs/{run_id}/progress")
def run_progress(run_id: str,
                 user: CurrentUser = Depends(get_current_user)) -> dict:
    """计划/步骤进度轮询（跨属主 404）。"""
    view = PlanRunService.progress(run_id, user.user_id)
    if view is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return view


@router.get("/runs/{run_id}/trace")
def run_trace(run_id: str,
              user: CurrentUser = Depends(get_current_user)) -> dict:
    """完整证据链：计划 + 每步摘要 + 降级原因（跨属主 404）。"""
    view = PlanRunService.trace(run_id, user.user_id)
    if view is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return view


@router.post("/runs/{run_id}/confirm")
def run_confirm(run_id: str, body: AgentConfirmIn,
                user: CurrentUser = Depends(get_current_user)):
    """确认/取消/改计划（§5.6.2）：原子查再置后立即 202，后台续跑。"""
    res = PlanRunService.confirm(run_id, user.user_id, body.action,
                                 modified_plan=body.modified_plan)
    if res is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return JSONResponse(status_code=202, content=res)


@router.post("/runs/{run_id}/cancel")
def run_cancel(run_id: str,
               user: CurrentUser = Depends(get_current_user)) -> dict:
    """执行中/挂起中取消（跨属主 404）。"""
    res = PlanRunService.cancel(run_id, user.user_id)
    if res is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return res


@router.get("/sessions/{session_id}/history")
def session_history(session_id: str,
                    user: CurrentUser = Depends(get_current_user)) -> list:
    """会话历史（用户原文 + 助手最终答案 + 代码摘要，§5.7；跨属主 404）。"""
    msgs = PlanRunService.history(session_id, user.user_id)
    if msgs is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return msgs


# ---------- v2.2 对话窗口：会话管理 / 附件 / 能力 / TTS ----------

@router.get("/sessions")
def list_sessions(include_archived: bool = False, archived_only: bool = False,
                  user: CurrentUser = Depends(get_current_user)) -> list:
    """当前用户的会话列表（最近活跃倒序；对话窗口默认只看活跃档）。"""
    from dao.models import AgentChatDAO
    from services.db import scoped
    with scoped() as conn:
        rows = AgentChatDAO(conn).list_sessions(
            user.user_id, limit=200,
            include_archived=include_archived,
            archived_only=archived_only)
    return [{"id": r["id"], "title": r["title"], "archived": bool(r["archived"]),
             "created_at": r["created_at"], "updated_at": r["updated_at"]}
            for r in rows]


@router.post("/sessions")
def create_session(body: SessionCreateIn,
                   user: CurrentUser = Depends(get_current_user)) -> dict:
    """新建空会话（「新建对话」按钮）。"""
    from dao.models import AgentChatDAO
    from services.db import scoped
    with scoped() as conn:
        sid = AgentChatDAO(conn).create_session(
            user.user_id, title=(body.title or "新对话")[:24])
    return {"id": sid, "title": (body.title or "新对话")[:24]}


@router.patch("/sessions/{session_id}")
def patch_session(session_id: str, body: SessionPatchIn,
                  user: CurrentUser = Depends(get_current_user)) -> dict:
    """会话改名 / 归档切换（至少提供一个字段；跨属主 404）。"""
    if body.title is None and body.archived is None:
        raise HTTPException(status_code=400, detail="无可更新字段")
    from dao.models import AgentChatDAO
    from services.db import scoped
    with scoped() as conn:
        dao = AgentChatDAO(conn)
        _own_session(dao, session_id, user.user_id)
        if body.title is not None:
            dao.rename_session(session_id, body.title.strip()[:64] or "新对话")
        if body.archived is not None:
            dao.set_session_archived(session_id, body.archived)
    return {"ok": True}


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str,
                   user: CurrentUser = Depends(get_current_user)) -> dict:
    """删除会话（物理删除消息/认知 run/步骤，不可逆；跨属主 404）。"""
    from dao.models import AgentChatDAO
    from services.db import scoped
    with scoped() as conn:
        dao = AgentChatDAO(conn)
        _own_session(dao, session_id, user.user_id)
        # 删除前兜底：未完结 run 先置 cancelled（轮询得终态而非 404 竞态）
        dao.cancel_active_runs(session_id)
        dao.delete_session(session_id)
    return {"ok": True}


@router.post("/uploads")
async def upload_attachment(file: UploadFile = File(...),
                            user: CurrentUser = Depends(get_current_user)) -> dict:
    """对话附件上传：魔数/大小校验（复用 upload_guard）后落
    `data/uploads/chat/`，不建 tasks 行。返回相对路径供 chat.attachments。"""
    from core.evidence import sanitize_filename
    from core.paths import data_path, to_rel
    from core.upload_guard import check_upload
    from services.task_entry import upload_limits
    import uuid

    data = await file.read()
    ok, err = check_upload(data, file.filename or "", upload_limits())
    if not ok:
        raise HTTPException(status_code=400, detail=err)
    save_dir = data_path("uploads", "chat")
    os.makedirs(save_dir, exist_ok=True)
    name = sanitize_filename(file.filename or "attachment", fallback="attachment")
    path = os.path.join(save_dir, f"{uuid.uuid4().hex[:8]}_{name}")
    with open(path, "wb") as f:
        f.write(data)
    return {"path": to_rel(path)}


@router.get("/model-info")
def model_info(user: CurrentUser = Depends(get_current_user)) -> dict:
    """模型/通道能力信息（前端能力弹窗数据源）：当前可用 provider、
    降级链档位、语音识别/合成可用性。"""
    from core.chat_client import get_chat_client
    from core.config import shared_config
    from core.tts_engine import TtsEngine
    client = get_chat_client()
    providers = [{"name": p.get("name"), "type": p.get("type")}
                 for p in getattr(client, "providers", []) or []]
    asr_cfg = shared_config().get("asr") or {}
    return {
        "provider_available": client.available_provider(),
        "providers": providers,
        "asr_available": bool(asr_cfg.get("enabled")
                              and asr_cfg.get("api_base")
                              and asr_cfg.get("api_key")),
        "tts_available": TtsEngine().available(),
    }


@router.post("/tts")
def tts(body: dict, user: CurrentUser = Depends(get_current_user)):
    """文本合成语音（mp3）。未配置 tts.* 返回 501（前端弹能力提示）。"""
    text = (body or {}).get("text") or ""
    if not text.strip():
        raise HTTPException(status_code=400, detail="文本为空")
    from core.tts_engine import TtsEngine
    engine = TtsEngine()
    if not engine.available():
        raise HTTPException(status_code=501, detail="语音合成能力未配置")
    audio = engine.synthesize(text)
    if audio is None:
        raise HTTPException(status_code=502,
                            detail=f"语音合成失败: {engine.last_error}")
    return Response(content=audio, media_type="audio/mpeg")
