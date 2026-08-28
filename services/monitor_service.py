"""后台 RTSP 自动轮询监控（daemon 线程）。

读取 monitor.* 配置；按 interval 对多路视频源抓帧并做实时轻链路检测，
critical 帧创建告警 + 证据截图 + 异步外部推送；按 (source, cls) 冷却去重，
确保持续违规时能按冷却周期重复告警，而不是等人工关闭后才会再报。

连接策略：MultiSourceMonitor 以 keep_open=True 持久持有各源长连接，
轮询间复用不重新 RTSP 握手（失败由 VideoSource.read 自动重连）；
抓帧在 grab_all 内并行、分析串行。本轮产生首个告警才开 SQLite 连接，
finally 统一关闭，避免逐告警建连与句柄泄漏。

实时性说明：interval_sec 是"采样节奏"，告警 SLA = 下一采样 ≤ interval_sec
（默认 3s）；它不是推理延迟。真正亚秒级响应走实时摄像头连续路径（ui/page_realtime）。
"""
from __future__ import annotations

import threading
import time

from core.realtime_engine import RealtimeEngine
from core.video_source import MultiSourceMonitor
from dao.db import get_conn, init_db
from services.task_service import TaskService

_MONITOR: "RtspMonitor | None" = None
_LOCK = threading.Lock()


class RtspMonitor:
    """单个后台轮询监控实例，线程安全地记录状态与计数。"""

    def __init__(self, sources: list[str], interval_sec: float = 3.0,
                 cooldown_sec: float = 60.0,
                 engine: RealtimeEngine | None = None,
                 db_path: str | None = None) -> None:
        self.sources = [s for s in (sources or []) if s and s.strip()]
        self.interval_sec = max(1.0, float(interval_sec))
        self.cooldown_sec = max(0.0, float(cooldown_sec))
        self.engine = engine or RealtimeEngine()
        self.db_path = db_path
        self.session_id = "rtsp_bg"
        # 长连接监控器：跨轮次复用底层 cv2 连接，stop() 时统一释放
        self._monitor = MultiSourceMonitor(self.sources, keep_open=True)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._last_alert: dict[tuple[str, str], float] = {}
        self.polls = 0
        self.alarms = 0
        self.last_error: str | None = None

    # ---------- 状态 ----------
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def status(self) -> dict:
        with self._lock:
            return {
                "running": self.running,
                "sources": list(self.sources),
                "interval_sec": self.interval_sec,
                "cooldown_sec": self.cooldown_sec,
                "polls": self.polls,
                "alarms": self.alarms,
                "last_error": self.last_error,
            }

    # ---------- 生命周期 ----------
    def start(self) -> None:
        if self.running or not self.sources:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
        self._thread = None
        # 及时断开底层 cv2 连接（RTSP/文件句柄），不等 GC
        try:
            self._monitor.release_all()
        except Exception:  # noqa: BLE001 释放失败不影响停止流程
            pass

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.poll_once()
            except Exception as exc:  # noqa: BLE001 单轮失败不终止线程
                with self._lock:
                    self.last_error = str(exc)[:200]
            self._stop_event.wait(self.interval_sec)

    # ---------- 单轮抓帧 ----------
    def poll_once(self) -> int:
        """抓取全部源并处理 critical 帧，返回本轮新增告警数。"""
        if not self.sources:
            return 0
        results = self._monitor.grab_all(self.engine.analyze, self.engine.draw)
        with self._lock:
            self.polls += 1
        created = 0
        conn = None  # 本轮懒建单连接：仅当真正要写告警时打开，finally 统一关闭
        try:
            for entry in results:
                if not entry.get("ok"):
                    continue
                comp = entry.get("compliance") or {}
                dets = entry.get("detections") or []
                if comp.get("level") != "critical" or not dets:
                    continue
                crit = [d for d in dets
                        if _sev_of(d.get("cls")) == "critical"]
                if not crit:
                    crit = [dets[0]]
                source = entry.get("source") or "rtsp"
                for d in crit:
                    key = (source, d.get("cls"))
                    now = time.time()
                    with self._lock:
                        last = self._last_alert.get(key, 0.0)
                        if now - last < self.cooldown_sec:
                            continue
                        self._last_alert[key] = now
                    try:
                        if conn is None:
                            conn = get_conn(self.db_path)
                            init_db(conn)
                        aid = TaskService(conn).raise_alarm(
                            session_id=self.session_id,
                            scene_id=d.get("scene"),
                            cls=d.get("cls"),
                            conf=d.get("conf"),
                            source=source,
                            annotated_bgr=entry.get("annotated"),
                            force=True,
                        )
                        if aid:
                            created += 1
                            with self._lock:
                                self.alarms += 1
                    except Exception as exc:  # noqa: BLE001
                        with self._lock:
                            self.last_error = str(exc)[:200]
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001 关闭失败不影响返回值
                    pass
        return created


def _sev_of(cls: str | None) -> str:
    from core.compliance import SEVERITY
    return SEVERITY.get(cls, "warning")


def get_monitor() -> RtspMonitor | None:
    """返回当前单例监控实例。"""
    return _MONITOR


def start_monitor(sources: list[str], interval_sec: float = 3.0,
                  cooldown_sec: float = 60.0) -> RtspMonitor:
    """启动后台轮询监控（重复调用返回已有实例）。"""
    global _MONITOR
    with _LOCK:
        if _MONITOR is not None and _MONITOR.running:
            return _MONITOR
        if _MONITOR is not None:
            _MONITOR.stop()
        _MONITOR = RtspMonitor(sources, interval_sec, cooldown_sec)
        _MONITOR.start()
        return _MONITOR


def stop_monitor() -> None:
    """停止并清空单例。"""
    global _MONITOR
    with _LOCK:
        if _MONITOR is not None:
            _MONITOR.stop()
            _MONITOR = None


def ensure_monitor_started() -> RtspMonitor | None:
    """按 monitor.* 配置启动后台监控；未启用或无源时返回 None。"""
    from core.config import shared_config
    conf = shared_config().get("monitor")
    if not isinstance(conf, dict) or not conf.get("enabled"):
        return None
    sources = [s for s in (conf.get("sources") or []) if s and s.strip()]
    if not sources:
        return None
    return start_monitor(
        sources,
        interval_sec=float(conf.get("interval_sec", 3) or 3),
        cooldown_sec=float(conf.get("cooldown_sec", 60) or 60),
    )
