"""实时链路门面（Phase 0）：实时页的引擎单例与视频源工具收口到服务层。

原 page_realtime 直接 import core.realtime_engine / core.video_source 并
在模块内维护单例——违反 ui→services 分层。本模块承载：
- RealtimeEngine 进程级单例（双检锁）与预热；
- analyze/draw/reset_tracking 转发；
- 视频源打码与连通性自检转发。
UI 只调本模块函数，拿到对象后按鸭子类型调用，不再 import core。
"""
from __future__ import annotations

import threading

from core.realtime_engine import RealtimeEngine
from core.video_source import MultiSourceMonitor
from core.video_source import check_source as _check_source
from core.video_source import mask_source as _mask_source

__all__ = ["get_engine", "prewarm", "mask_source",
           "check_source", "MultiSourceMonitor", "set_hub_active",
           "hub_active"]

_LOCK = threading.Lock()
_ENGINE: RealtimeEngine | None = None
# Phase 4：实时 Hub 接管标志——api.realtime_hub 启动/停止时置位，
# monitor_service 据此跳过后台轮询（同进程内避免双路推理）。
_HUB_ACTIVE = threading.Event()


def set_hub_active(active: bool) -> None:
    """api.realtime_hub 生命周期回调：Hub 运行中置位/停止时清除。"""
    if active:
        _HUB_ACTIVE.set()
    else:
        _HUB_ACTIVE.clear()


def hub_active() -> bool:
    """本进程是否已由实时 Hub 接管视频源推理（api 进程为真）。"""
    return _HUB_ACTIVE.is_set()


def get_engine() -> RealtimeEngine:
    """进程级单例（双检锁）：并发首访只建一次。"""
    global _ENGINE
    if _ENGINE is None:
        with _LOCK:
            if _ENGINE is None:
                _ENGINE = RealtimeEngine()
    return _ENGINE


def prewarm() -> int:
    """启动期预热：构建双场景检测头；失败置空由首请求重试。返回头数。"""
    from core.logging import get_logger
    log = get_logger(__name__)
    global _ENGINE
    try:
        eng = get_engine()
        n = len(eng.engines)
        log.info(f"[prewarm] YOLO 双场景检测头预热完成：{n} 个检测头已就绪"
                 if n else "[prewarm] 预热完成但未加载到任何检测头（检查权重路径）")
        return n
    except Exception as exc:  # noqa: BLE001 预热失败不影响页面按需重试
        _ENGINE = None
        log.warning(f"[prewarm] YOLO 预热失败（将由首请求重试）: {exc}")
        return 0


def mask_source(source: str) -> str:
    return _mask_source(source)


def check_source(source: str, timeout: float = 5.0) -> dict:
    return _check_source(source, timeout)
