"""FastAPI 应用工厂（Phase 2）：/api 前缀挂 routers，frontend/dist 存在才挂静态。

启动顺序约束：必须在 import 任何 agents/core 前设置 CPU 抑制环境变量——
与 app.py / scripts/run_tests.py 同一套配置（torch+onnxruntime 同进程
多线程原生段错误抑制，勿动）。主进程不 import torch：BGE 已隔离到
core.bge_worker 子进程。

运行：python -m uvicorn api.main:app --host 0.0.0.0 --port 8000
Swagger 文档（仅开发）：http://localhost:8000/docs
"""
from __future__ import annotations

import os

# 与 app.py 完全一致的抑制配置（在 import 前生效）
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("TQDM_DISABLE", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from api.routers import (admin, alarms, auth, history, media, orders,
                         reports, tasks, ws)
from core.config import ConfigError, shared_config  # 白名单（情况1）：只读配置
from core.logging import get_logger      # 白名单（情况1）：日志
from services.permission_service import AuthorizationError as ServicePermissionError

log = get_logger(__name__)

_API_PREFIX = "/api"
_ROOT = Path(__file__).resolve().parent.parent
_DIST = _ROOT / "frontend" / "dist"


def _background_prewarm() -> None:
    """启动后台预热（与 app.py 登录后自举同序）：YOLO/RTSP监控/LLM/BGE。

    全部 best-effort：任一失败不影响 API 服务，由首请求按需重试。
    环境变量 API_PREWARM=0 可关闭（测试/极简部署用）。
    """
    if os.environ.get("API_PREWARM", "1").strip() == "0":
        return

    def _run() -> None:
        try:
            from services import realtime_entry
            realtime_entry.prewarm()
        except Exception as exc:  # noqa: BLE001 预热失败不阻断启动
            log.warning(f"YOLO 检测头预热失败: {exc}")
        try:
            from services import monitor_service
            monitor_service.ensure_monitor_started()
        except Exception as exc:  # noqa: BLE001
            log.warning(f"后台监控启动失败: {exc}")
        try:
            from core.llm_engine import LlmEngine
            LlmEngine().warmup()
        except Exception as exc:  # noqa: BLE001
            log.warning(f"LLM 预热失败（润色将降级模板）: {exc}")
        try:
            from core.rag_engine import RagEngine
            RagEngine.preload()
        except Exception as exc:  # noqa: BLE001
            log.warning(f"BGE 预热失败（RAG 将降级跳过）: {exc}")

    import threading
    threading.Thread(target=_run, daemon=True, name="api-prewarm").start()


@asynccontextmanager
async def _lifespan(_: FastAPI):
    try:
        from services import session_entry
        session_entry.ensure_ready()
    except Exception as exc:  # noqa: BLE001 自举失败不阻断进程，但必须留痕
        log.warning(f"启动自举失败（建库/种子账号/模型注册）: {exc}")
    hub = None
    try:
        # Phase 4：实时 Hub（后端唯一推理循环）——config.realtime.enabled
        # 才启动；API_PREWARM=0（测试/极简部署）一并跳过后台工作负载
        if (os.environ.get("API_PREWARM", "1").strip() != "0"
                and bool((shared_config().get("realtime") or {})
                         .get("enabled", False))):
            from api.realtime_hub import start_hub
            hub = start_hub()
    except Exception as exc:  # noqa: BLE001 Hub 启动失败不影响 API 可用
        log.warning(f"实时 Hub 启动失败: {exc}")
    _background_prewarm()
    yield
    if hub is not None:
        try:
            hub.stop()
        except Exception:  # noqa: BLE001
            log.warning("实时 Hub 停止异常")


def _install_error_handlers(app: FastAPI) -> None:
    """服务层异常 → HTTP 语义：权限不足 403 / 入参业务错误 400 / 配置缺失 503。"""
    @app.exception_handler(ServicePermissionError)
    async def _perm(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    @app.exception_handler(ValueError)
    async def _bad_request(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(ConfigError)
    async def _config(_: Request, exc: ConfigError) -> JSONResponse:
        return JSONResponse(status_code=503,
                            content={"detail": f"配置不可用: {exc}"})

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception(f"未处理异常 {request.method} {request.url.path}: {exc}")
        return JSONResponse(status_code=500,
                            content={"detail": "服务器内部错误"})


def _maybe_cors(app: FastAPI) -> None:
    """仅开发模式放行 Vite dev server（localhost:5173）；生产默认关闭。

    开关：环境变量 API_DEV_CORS=1 或 config.api.dev_cors=true。
    """
    env_on = os.environ.get("API_DEV_CORS", "").strip() == "1"
    conf = shared_config().get("api") or {}
    conf_on = bool(conf.get("dev_cors", False))
    if not (env_on or conf_on):
        return
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    log.info("开发模式 CORS 已放行 http://localhost:5173")


def _mount_frontend(app: FastAPI) -> None:
    """frontend/dist 存在才托管（Phase 3 产物；单进程单端口部署）。

    /assets 直出带指纹的构建产物；其余 GET 路径：命中 dist 内真实文件则
    直出（favicon 等），否则回 index.html——SPA history 路由深链路可用。
    API 路由先注册先匹配，不受此兜底影响；防穿越：解析结果必须仍在 dist 内。
    """
    if not _DIST.is_dir():
        return
    assets = _DIST / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets)),
                  name="assets")

    dist_root = str(_DIST.resolve())
    index = _DIST / "index.html"

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> FileResponse:
        # API 未匹配路径不回 index.html（保持 404 语义，防前端兜底吞掉
        # API 层的路径错误/穿越探测）；SPA 路由不会以 /api 开头
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        if full_path:
            candidate = (_DIST / full_path).resolve()
            if candidate.is_file() and str(candidate).startswith(dist_root):
                return FileResponse(candidate)
        return FileResponse(index)


def create_app() -> FastAPI:
    app = FastAPI(
        title="智护工地 API",
        version="1.0.0",
        description="施工安全 AI 监控系统（动火作业 + 施工 PPE）。"
                    "认证：POST /api/auth/login 取 JWT，"
                    "后续请求带 Authorization: Bearer <token>。离线内网部署。",
        lifespan=_lifespan,
    )
    _install_error_handlers(app)
    _maybe_cors(app)

    @app.get("/healthz", tags=["meta"])
    async def healthz() -> dict:
        return {"status": "ok", "app": "zhihu-gongdi-api",
                "version": app.version}

    for mod in (auth, tasks, alarms, orders, reports, admin, history,
                media, ws):
        app.include_router(mod.router, prefix=_API_PREFIX)

    # 前端构建产物存在才挂载（Phase 3 产物；SPA fallback 含在内）
    _mount_frontend(app)
    return app


app = create_app()
