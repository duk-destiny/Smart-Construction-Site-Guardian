"""工具注册表与工具层（设计文档 §5.4）：现有能力整体降级为被调用的工具。

统一接口四要素：名称、给 LLM 看的描述、参数 schema（pydantic 二次校验）、
返回结构。工具返回结构化 JSON（{"status","data","error"}），不返回自然语言。

安全边界（§5.4 / §5.8 / §5.13）：
- 工具均为薄封装，自持 `scoped()` 连接（不复用调用方连接，避免跨线程悬空）；
- 用户作用域（user_id/role）由代码经 ToolCtx 注入，不经 LLM 之手；
- `side_effect=True` 的工具由内核强制挂起人工确认，无豁免开关；
- `run_video_pipeline` 仅包到 `Orchestrator.execute()` 返回为止，
  不含建单落库（建单经副作用工具 + 人工确认；§5.4 边界）。
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Literal

from pydantic import BaseModel, Field

from core.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class ToolCtx:
    """执行上下文：由代码注入的用户作用域（§5.13：作用域不经 LLM 决定）。"""

    user_id: str
    role: str = ""
    run_id: str | None = None


@dataclass(frozen=True)
class ToolSpec:
    """工具规格：函数 + 给 LLM 看的描述 + 参数 schema + 副作用/预算标记。"""

    fn: Callable[[dict, ToolCtx], dict]
    desc: str                                    # 给 LLM 看的能力描述
    args_schema: type[BaseModel]                 # 入参二次校验
    side_effect: bool = False                    # True → 强制人工确认
    timeout_sec: float = 15.0                    # 步级墙钟预算（被剩余总预算裁剪）
    max_concurrency: int = 0                     # >0 用 Semaphore 串行化


# ---------- 参数 schema（每工具一个，越界即拒）----------

class WeeklyReportArgs(BaseModel):
    start: str | None = Field(default=None, max_length=10)
    end: str | None = Field(default=None, max_length=10)


class LookupViewsArgs(BaseModel):
    view: Literal["detail_view", "list_view", "overdue_rows"]
    order_id: str | None = None
    statuses: list[Literal["open", "rejected", "submitted", "closed"]] = Field(
        default_factory=lambda: ["open", "rejected", "submitted"])
    limit: int = Field(default=10, ge=1, le=50)
    as_of: str | None = Field(default=None, max_length=19)


class RagSearchArgs(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    top_k: int = Field(default=3, ge=1, le=10)
    collection: str | None = Field(default=None, max_length=64)


class VideoPipelineArgs(BaseModel):
    """mode（§5.10）：full=完整链路（默认）；quick=跳过二阶段 RAG 提速追问。"""

    video: str | None = None
    images: list[str] = Field(default_factory=list)
    permit_info: dict = Field(default_factory=dict)
    mode: Literal["full", "quick"] = "full"


class RaiseSuggestionArgs(BaseModel):
    suggestion: str = Field(min_length=1, max_length=500)
    reason: str = Field(default="", max_length=200)
    category: str = Field(default="整改建议", max_length=32)


class OrderDraftArgs(BaseModel):
    """工单草稿：只进待审队列，不落正式 work_orders（正式建单走既有人工流程）。"""

    hazard_desc: str = Field(min_length=1, max_length=500)
    clause: str = Field(default="", max_length=500)
    requirement: str = Field(default="", max_length=500)


# ---------- 工具实现（薄封装，自持连接）----------

def _require_view(ctx: ToolCtx) -> None:
    """只读工具也继承用户数据权限（§5.13）：无 view 权限即拒。"""
    from services.db import scoped
    from services.permission_service import PermissionService
    with scoped() as conn:
        PermissionService(conn).require(ctx.user_id, "view")


def _tool_weekly_report_data(args: dict, ctx: ToolCtx) -> dict:
    """周报统计：确定性 SQL 聚合（WeeklyReportService.gather），零 LLM。"""
    _require_view(ctx)
    from services.db import scoped
    from services.report_service import WeeklyReportService
    with scoped() as conn:
        stats = WeeklyReportService(conn).gather(args.get("start"), args.get("end"))
    return {"status": "success", "data": stats, "error": None}


def _tool_lookup_views(args: dict, ctx: ToolCtx) -> dict:
    """IntentRouter 三只读视图：工单详情 / 列表 / 逾期行（路由层禁 LLM）。"""
    _require_view(ctx)
    from services.db import scoped
    from services.intent_router import IntentRouter
    view = args["view"]
    with scoped() as conn:
        router = IntentRouter(conn, use_llm=False)
        if view == "detail_view":
            if not args.get("order_id"):
                return {"status": "failed", "data": None,
                        "error": "detail_view 需要 order_id"}
            data = router.detail_view(args["order_id"])
            if data is None:
                return {"status": "failed", "data": None,
                        "error": f"工单 {args['order_id']} 不存在"}
            return {"status": "success", "data": data, "error": None}
        if view == "list_view":
            rows = router.list_view(
                statuses=tuple(args.get("statuses")
                               or ("open", "rejected", "submitted")),
                limit=int(args.get("limit") or 10))
            return {"status": "success", "data": {"orders": rows}, "error": None}
        # overdue_rows
        as_of = args.get("as_of") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rows = router.overdue_rows(as_of, limit=int(args.get("limit") or 20))
        return {"status": "success", "data": {"overdue": rows, "as_of": as_of},
                "error": None}


def _tool_rag_search(args: dict, ctx: ToolCtx) -> dict:
    """规范知识库向量检索（只读）；检索不可用时降级返回空集，不拖垮整 run。"""
    _require_view(ctx)
    try:
        from core.rag_engine import RagEngine
        eng = (RagEngine(collection_name=args["collection"])
               if args.get("collection") else RagEngine())
        items = eng.query(args["query"], top_k=int(args.get("top_k") or 3))
        return {"status": "success", "data": {"items": items}, "error": None}
    except Exception as exc:  # noqa: BLE001 检索层失败 → 步骤级降级
        return {"status": "degraded", "data": {"items": []},
                "error": f"{type(exc).__name__}: {exc}"}


# VideoAnalysisShell 进程内检测缓存（§5.10）：严格进程内、不落盘，
# 双进程（Streamlit/FastAPI）各自独立实例；仅视频对话场景 opt-in，
# 上传主链路不经过本工具，不受影响。
_VIDEO_CACHE = None
_VIDEO_CACHE_LOCK = threading.Lock()


def get_video_cache():
    """懒加载进程内单例缓存（供测试/诊断引用）。"""
    global _VIDEO_CACHE
    with _VIDEO_CACHE_LOCK:
        if _VIDEO_CACHE is None:
            from pipeline.detection_cache import DetectionCache
            _VIDEO_CACHE = DetectionCache()
        return _VIDEO_CACHE


def _tool_run_video_pipeline(args: dict, ctx: ToolCtx) -> dict:
    """VideoAnalysisShell（§5.10）：查缓存 → 按 mode 编排 → 收成结构化摘要。

    边界仍止于 `Orchestrator.execute` 返回，不含建单落库；
    检测缓存仅本壳显式注入（上传主链路默认关闭）。
    """
    from pipeline.detection_cache import DetectionCache
    from pipeline.orchestrator import Orchestrator
    from pipeline.vision import VisionStage

    mode = args.get("mode") or "full"
    if mode not in ("full", "quick"):   # schema 已拒，此处双保险（越界即拒）
        return {"status": "failed", "data": None,
                "error": f"非法执行模式 {mode!r}"}

    cache = get_video_cache()
    permit_info = dict(args.get("permit_info") or {})
    scene_id = str(permit_info.get("scene") or "hot_work")
    images = list(args.get("images") or [])
    video = args.get("video")

    # 1) 查缓存：按文件内容 hash（重新上传即新 key，天然隔离）
    input_files = ([video] if video else []) + images
    keys = {p: DetectionCache.key_of_file(p) for p in input_files}
    cache_hits = sum(
        1 for p in input_files
        if keys.get(p) is not None and cache.get(keys[p]) is not None)

    # 2) 按 mode 编排：注入缓存的 VisionStage（仅本壳 opt-in）
    vision = VisionStage(scene_id=scene_id, cache=cache)
    orch = Orchestrator(vision=vision, scene_id=scene_id)
    msg = orch.execute(
        ctx.run_id or f"chat_{ctx.user_id}",
        images=images, video=video, permit_info=permit_info, mode=mode)

    payload = getattr(msg, "payload", None) or {}
    vision_payload = (payload.get("vision") or {}).get("payload") or {}
    rule_payload = (payload.get("rule") or {}).get("payload") or {}
    review_payload = (payload.get("review") or {}).get("payload") or {}
    detections = vision_payload.get("detections") or []
    compliance = rule_payload.get("compliance") or []
    work_order = payload.get("work_order") or {}

    # 3) 检测结果按视频文件 hash 回写（多轮追问不重跑检测）
    if video and keys.get(video) and getattr(msg, "status", "") != "failed":
        cache.put(keys[video], {"detections": detections})

    # 4) 收成结构化摘要：场景/风险等级/证据条数/工单要点（仅展示，不建单）
    data = {
        "pipeline_status": getattr(msg, "status", "unknown"),
        "mode": mode,
        "scene_id": scene_id,
        "risk_level": payload.get("risk_level"),      # 融合查表结果，仅展示
        "reasons": payload.get("reasons") or [],
        "evidence_count": {
            "detections": len(detections),
            "compliance": len(compliance),
        },
        "violation_descs": vision_payload.get("violation_descs") or [],
        "work_order_points": {
            "risk_level": work_order.get("risk_level"),
            "hazard_desc": work_order.get("hazard_desc"),
            "clause": work_order.get("clause"),
            "requirement": work_order.get("requirement"),
        },
        "needs_review": review_payload.get("needs_review", False),
        "cache": {
            "hits": cache_hits,
            "total_inputs": len(input_files),
            "size": len(cache),
        },
    }
    status = "success" if getattr(msg, "status", "") == "success" else "degraded"
    return {"status": status, "data": data, "error": getattr(msg, "error", None)}


def _tool_raise_suggestion(args: dict, ctx: ToolCtx) -> dict:
    """副作用工具：生成整改/告警建议，只写待审队列（由内核承载于
    agent_chat_runs.confirm_payload），不直接落正式业务表。"""
    return {"status": "success",
            "data": {"queued": True, "kind": "suggestion",
                     "suggestion": args["suggestion"],
                     "reason": args.get("reason", ""),
                     "category": args.get("category", "整改建议"),
                     "raised_by": ctx.user_id},
            "error": None}


def _tool_create_order_draft(args: dict, ctx: ToolCtx) -> dict:
    """副作用工具：生成工单草稿（不建正式单）。

    铁律：LLM 不进风险定级——草稿不携带定级结论，风险等级由人工确认
    建单时经 compliance.severity 查表确定。
    """
    return {"status": "success",
            "data": {"queued": True, "kind": "order_draft",
                     "hazard_desc": args["hazard_desc"],
                     "clause": args.get("clause", ""),
                     "requirement": args.get("requirement", ""),
                     "raised_by": ctx.user_id},
            "error": None}


# ---------- 注册表 ----------

TOOL_REGISTRY: dict[str, ToolSpec] = {
    "weekly_report_data": ToolSpec(
        fn=_tool_weekly_report_data,
        desc="查询周期安全统计（检测帧/告警/工单闭环/逾期/按责任人），"
             "入参 start/end 为 YYYY-MM-DD，可空=全量。只读。",
        args_schema=WeeklyReportArgs, side_effect=False, timeout_sec=10.0),
    "lookup_views": ToolSpec(
        fn=_tool_lookup_views,
        desc="查询工单只读视图：view=detail_view(需 order_id)/"
             "list_view(statuses,limit)/overdue_rows(as_of)。只读。",
        args_schema=LookupViewsArgs, side_effect=False, timeout_sec=8.0),
    "rag_search": ToolSpec(
        fn=_tool_rag_search,
        desc="在施工安全规范知识库中做语义检索，返回相关条款。只读。",
        args_schema=RagSearchArgs, side_effect=False, timeout_sec=30.0),  # 30s 容忍 BGE 子进程冷启动（~11s+）；仍被 run 剩余预算裁剪
    "run_video_pipeline": ToolSpec(
        fn=_tool_run_video_pipeline,
        desc="对视频/图像跑完整研判链路（检测+规范+融合+复核），"
             "mode=full 完整链路 / quick 跳过二阶段规范检索；"
             "同文件检测结果进程内缓存，多轮追问不重跑检测；返回结构化结论；不建单。",
        args_schema=VideoPipelineArgs, side_effect=False,
        timeout_sec=15.0, max_concurrency=1),
    "raise_suggestion": ToolSpec(
        fn=_tool_raise_suggestion,
        desc="提出整改/告警建议并写入待审队列（需人工确认后才生效）。",
        args_schema=RaiseSuggestionArgs, side_effect=True, timeout_sec=5.0),
    "create_order_draft": ToolSpec(
        fn=_tool_create_order_draft,
        desc="生成工单草稿（不建正式单，需人工确认后走既有建单流程）。",
        args_schema=OrderDraftArgs, side_effect=True, timeout_sec=5.0),
}

# max_concurrency>0 的工具按名称共享信号量（进程内串行化）
_SEMAPHORES: dict[str, threading.Semaphore] = {}
_SEM_LOCK = threading.Lock()


def _semaphore_for(name: str, limit: int) -> threading.Semaphore:
    with _SEM_LOCK:
        sem = _SEMAPHORES.get(name)
        if sem is None:
            sem = threading.Semaphore(max(1, limit))
            _SEMAPHORES[name] = sem
        return sem


def invoke_tool(name: str, spec: ToolSpec, args: dict, ctx: ToolCtx,
                timeout_sec: float) -> dict:
    """统一工具调用：信号量串行化 + 墙钟超时（超时→degraded，崩溃→failed）。"""
    def _call() -> dict:
        if spec.max_concurrency > 0:
            with _semaphore_for(name, spec.max_concurrency):
                return spec.fn(args, ctx)
        return spec.fn(args, ctx)

    ex = ThreadPoolExecutor(max_workers=1)
    try:
        future = ex.submit(_call)
        try:
            out = future.result(timeout=max(timeout_sec, 0.1))
        except FuturesTimeout:
            return {"status": "degraded", "data": None,
                    "error": f"工具 {name} 超时({timeout_sec:.1f}s)"}
        except Exception as exc:  # noqa: BLE001 工具崩溃 → failed 留痕
            return {"status": "failed", "data": None,
                    "error": f"{type(exc).__name__}: {exc}"}
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    if not isinstance(out, dict):
        return {"status": "failed", "data": None,
                "error": f"工具 {name} 返回了非结构化结果"}
    out.setdefault("status", "success")
    if out.get("status") not in ("success", "degraded", "failed"):
        out["status"] = "failed"
    return out
