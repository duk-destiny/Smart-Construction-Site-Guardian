"""实时 Hub（Phase 4）：后端唯一推理循环 + WebSocket 帧广播的内存交换站。

架构：
- 常驻 daemon 线程按配置 fps 抓取全部视频源（monitor.sources，空则回退
  demo:// 合成源），对每帧执行 检测→误报过滤→per-source 跟踪→三级合规
  （core.realtime_engine，纯规则，不调 RAG/LLM——实时轻链路铁律不变）；
- 告警当帧出：critical 帧经 services.history_service.raise_critical_alarm
  走既有告警链路（建告警→证据→异步推送→条款挂载），Hub 侧按 (源, 类别)
  冷却去重防刷屏；帧级历史持久化复用 history_service.record_frame；
- 广播模型：Hub 只维护「每源最新帧」状态（seq 递增），WS 连接各自轮询
  latest() 取走新帧——无队列积压问题，N 个观看者天然共享同一路推理；
- 无人观看降频：viewer 计数为 0 时按 idle_fps 保活（默认 1fps），有观看者
  时恢复 active_fps；此行为由 config.realtime.* 配置。

与 monitor_service 的收敛：Hub 启动即置 services.realtime_entry 的接管
标志，monitor_service.ensure_monitor_started 检测到后跳过后台轮询——
同进程内只有一路推理（Streamlit 进程不受影响，仍走旧轮询链路）。
"""
from __future__ import annotations

import base64
import threading
import time
from dataclasses import dataclass, field

import cv2

from core.compliance import SEVERITY
from core.config import shared_config
from core.logging import get_logger
from core.realtime_engine import RealtimeEngine
from core.video_source import MultiSourceMonitor, mask_source
from services import history_service, realtime_entry

log = get_logger(__name__)


@dataclass
class FrameState:
    """单源最新帧的广播状态（不可变快照，WS 侧整包取走）。"""

    seq: int                          # 源内递增序号（客户端据此去重）
    source: str                       # 原始源串（仅服务端内部使用）
    jpeg_b64: str                     # 标注后的 JPEG（框已画好）base64
    status: str                       # 合规/警告/不合规（帧级判定）
    level: str                        # safe/warning/critical
    boxes: list[dict] = field(default_factory=list)   # 违规/警告框摘要
    alarms: list[dict] = field(default_factory=list)  # 本帧新产生的告警
    cost_ms: int = 0
    ts: float = 0.0


class RealtimeHub:
    """多路帧源的唯一推理循环（进程级单例，见 get_hub）。"""

    def __init__(self, sources: list[str],
                 engine: RealtimeEngine | None = None,
                 active_fps: float = 2.0, idle_fps: float = 1.0,
                 jpeg_quality: int = 70, cooldown_sec: float = 60.0) -> None:
        self.sources = [s for s in (sources or []) if s and s.strip()]
        self.active_fps = max(0.2, float(active_fps))
        self.idle_fps = max(0.1, float(idle_fps))
        self.jpeg_quality = max(30, min(95, int(jpeg_quality)))
        self.cooldown_sec = max(0.0, float(cooldown_sec))
        # 懒加载：默认在 Hub 线程首帧时才构建引擎（YOLO 会话构建 1-3s，
        # 不阻塞 start() 调用方/应用启动）；测试可注入 stub engine。
        self._engine = engine
        self.session_prefix = "hub"
        self._monitor = MultiSourceMonitor(self.sources, keep_open=True)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._state: dict[int, FrameState] = {}      # index -> latest
        self._seq: dict[int, int] = {}
        self._viewers = 0
        self._last_alert: dict[tuple[int, str], float] = {}
        self.polls = 0
        self.alarms = 0
        self.last_error: str | None = None

    # ---------- 生命周期 ----------

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running or not self.sources:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="realtime-hub")
        self._thread.start()
        realtime_entry.set_hub_active(True)
        log.info(f"实时 Hub 已启动：{len(self.sources)} 路源 "
                 f"(active {self.active_fps:.1f}fps / idle {self.idle_fps:.1f}fps)")

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self._thread = None
        realtime_entry.set_hub_active(False)
        try:
            self._monitor.release_all()
        except Exception:  # noqa: BLE001 释放失败不影响停止流程
            pass
        log.info("实时 Hub 已停止")

    # ---------- 观看者（驱动降频策略） ----------

    def add_viewer(self) -> None:
        with self._lock:
            self._viewers += 1

    def remove_viewer(self) -> None:
        with self._lock:
            self._viewers = max(0, self._viewers - 1)

    @property
    def viewers(self) -> int:
        with self._lock:
            return self._viewers

    @property
    def engine(self) -> RealtimeEngine:
        if self._engine is None:
            self._engine = realtime_entry.get_engine()
        return self._engine

    def _target_fps(self) -> float:
        return self.active_fps if self.viewers > 0 else self.idle_fps

    # ---------- 推理主循环 ----------

    def _loop(self) -> None:
        while not self._stop.is_set():
            t0 = time.time()
            try:
                self.cycle()
            except Exception as exc:  # noqa: BLE001 单轮失败不终止线程
                with self._lock:
                    self.last_error = str(exc)[:200]
                log.warning(f"实时 Hub 单轮失败: {exc}")
            elapsed = time.time() - t0
            self._stop.wait(max(0.0, 1.0 / self._target_fps() - elapsed))

    def cycle(self) -> int:
        """抓取全部源 → 逐源分析 → 告警/持久化/发布，返回本轮新告警数。

        抓帧并行（IO，各源独立连接）；分析串行——引擎侧 onnxruntime 会话
        已按核数封顶，且告警链路依赖全局去重，串行是当前 CPU 预算下最稳
        的调度（per-source tracker 已为并行 analyze 解锁，后续可按需切并行）。
        """
        if not self.sources:
            return 0
        # 阶段一：并行抓帧（复用 MultiSourceMonitor 的长连接源包装）
        reads: list[tuple[int, bool, object]] = []

        def _read(idx: int):
            ok, frame = self._monitor.sources[idx].read()
            return idx, ok, frame

        from concurrent.futures import ThreadPoolExecutor
        workers = max(1, min(4, len(self.sources)))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            reads = list(ex.map(_read, range(len(self.sources))))

        created = 0
        now = time.time()
        for idx, ok, frame in sorted(reads):
            if not ok or frame is None:
                continue
            t0 = time.time()
            source = self.sources[idx]
            dets, comp = self.engine.analyze(frame, source_key=source)
            cost = int((time.time() - t0) * 1000)
            annotated = self.engine.draw(frame, comp)

            # 帧级历史持久化（失败留痕不中断；services.history_service 收口）
            history_service.record_frame(f"{self.session_prefix}_{idx}",
                                         comp["status"], dets, mode="realtime")

            # 告警当帧出：critical 项走既有告警链路；(源, 类别) 冷却去重
            alarms: list[dict] = []
            if comp.get("level") == "critical" and dets:
                for d in [d for d in dets
                          if SEVERITY.get(d.get("cls"), "warning") == "critical"] or dets[:1]:
                    key = (idx, d.get("cls") or "")
                    if now - self._last_alert.get(key, 0.0) < self.cooldown_sec:
                        continue
                    self._last_alert[key] = now
                    try:
                        aid = history_service.raise_critical_alarm(
                            session_id=f"{self.session_prefix}_{idx}",
                            dets=[d],
                            source=source,
                            annotated_bgr=annotated,
                        )
                        if aid:
                            created += 1
                            with self._lock:
                                self.alarms += 1
                            alarms.append({
                                "id": aid, "cls": d.get("cls"),
                                "conf": d.get("conf"),
                                "label": d.get("violation_desc", d.get("cls")),
                            })
                    except Exception as exc:  # noqa: BLE001 单条告警失败不中断
                        log.warning(f"Hub 告警触发失败（{mask_source(source)}）: {exc}")

            boxes = [
                {"label": it.get("label"), "conf": it.get("conf"),
                 "severity": it.get("severity"),
                 "bbox": it.get("bbox"), "track_id": it.get("track_id")}
                for it in (comp.get("violations") or [])
            ]
            state = FrameState(
                seq=self._next_seq(idx), source=source,
                jpeg_b64=_encode_jpeg(annotated, self.jpeg_quality),
                status=comp.get("status", ""), level=comp.get("level", "safe"),
                boxes=boxes, alarms=alarms, cost_ms=cost, ts=now,
            )
            with self._lock:
                self._state[idx] = state
                self.polls += 1
        return created

    def _next_seq(self, idx: int) -> int:
        with self._lock:
            self._seq[idx] = self._seq.get(idx, 0) + 1
            return self._seq[idx]

    # ---------- 广播状态读取（WS 侧调用） ----------

    def latest(self, idx: int) -> FrameState | None:
        with self._lock:
            return self._state.get(idx)

    def source_list(self) -> list[dict]:
        """源清单（展示层打码凭据）。"""
        return [{"index": i, "source": mask_source(s)}
                for i, s in enumerate(self.sources)]

    def status(self) -> dict:
        with self._lock:
            base = {
                "running": self.running,
                "viewers": self._viewers,
                "polls": self.polls,
                "alarms": self.alarms,
                "last_error": self.last_error,
                "active_fps": self.active_fps,
                "idle_fps": self.idle_fps,
            }
        base["sources"] = self.source_list()
        base["target_fps"] = self._target_fps()
        return base


def _encode_jpeg(frame, quality: int) -> str:
    ok, buf = cv2.imencode(".jpg", frame,
                           [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        return ""
    return base64.b64encode(buf.tobytes()).decode()


# ---------- 进程级单例 ----------

_HUB: RealtimeHub | None = None
_HUB_LOCK = threading.Lock()


def _sources_from_config() -> list[str]:
    """视频源解析：realtime.sources 优先，空则回退 monitor.sources，
    再空则 demo:// 合成源兜底（无摄像头环境开箱可演示）。"""
    conf = shared_config().get("realtime") or {}
    sources = [str(s).strip() for s in (conf.get("sources") or [])
               if str(s).strip()]
    if not sources:
        sources = [str(s).strip() for s in
                   ((shared_config().get("monitor") or {}).get("sources") or [])
                   if str(s).strip()]
    return sources or ["demo://"]


def build_hub() -> RealtimeHub:
    """按 config.realtime.* 构造 Hub（不启动）。"""
    conf = shared_config().get("realtime") or {}
    return RealtimeHub(
        _sources_from_config(),
        active_fps=float(conf.get("active_fps", 2) or 2),
        idle_fps=float(conf.get("idle_fps", 1) or 1),
        jpeg_quality=int(conf.get("jpeg_quality", 70) or 70),
        cooldown_sec=float(conf.get("cooldown_sec", 60) or 60),
    )


def start_hub() -> RealtimeHub | None:
    """启动进程级 Hub 单例（已运行或无源则复用/跳过）。"""
    global _HUB
    with _HUB_LOCK:
        if _HUB is not None and _HUB.running:
            return _HUB
        if _HUB is not None:
            _HUB.stop()
        hub = build_hub()
        hub.start()
        _HUB = hub
        return hub


def stop_hub() -> None:
    global _HUB
    with _HUB_LOCK:
        if _HUB is not None:
            _HUB.stop()
            _HUB = None


def get_hub() -> RealtimeHub | None:
    """返回运行中的 Hub（未启动返回 None——WS 端据此报告不可用）。"""
    return _HUB if (_HUB is not None and _HUB.running) else None
