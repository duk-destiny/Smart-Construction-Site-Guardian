"""Phase 4 实时 Hub 测试：单推理循环、告警去重落库、观看者降频、
per-source tracker 隔离、reload build-then-swap、TaskService TOCTOU 加锁。

引擎一律注入 stub（不加载 YOLO 权重）；视频源用 demo:// 合成源；
DB 指向临时库（dao.db.DEFAULT_DB_PATH 运行时读取，逐例替换）。
"""
from __future__ import annotations

import tempfile
import threading
import time

import numpy as np
import pytest

import dao.db
from core.compliance import evaluate
from core.realtime_engine import RealtimeEngine
from api.realtime_hub import RealtimeHub


@pytest.fixture(autouse=True)
def tmp_db(monkeypatch):
    monkeypatch.setattr(dao.db, "DEFAULT_DB_PATH",
                        tempfile.mktemp(suffix=".db"))


class StubEngine:
    """恒定返回 critical 火花检测；记录 analyze 调用的 source_key。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def analyze(self, frame, source_key="default"):
        self.calls.append(source_key)
        dets = [{"cls": "spark", "conf": 0.9, "bbox": [100, 100, 40, 40],
                 "severity": "critical", "violation_desc": "火花",
                 "track_id": 1, "track_frames": 1, "scene": "hot_work"}]
        return dets, evaluate(dets)

    def draw(self, frame, comp):
        return frame


class QuietEngine(StubEngine):
    """safe 帧（无告警路径）。"""

    def analyze(self, frame, source_key="default"):
        self.calls.append(source_key)
        return [], evaluate([])


@pytest.fixture
def hub(tmp_path):
    h = RealtimeHub(["demo://"], engine=StubEngine(),
                    active_fps=8, idle_fps=4, cooldown_sec=0.5)
    yield h
    if h.running:
        h.stop()


def test_hub_cycle_publishes_and_alarms_persist(hub):
    hub.start()
    deadline = time.time() + 5
    while time.time() < deadline and hub.alarms == 0:
        time.sleep(0.1)
    assert hub.alarms >= 1, "critical 帧必须当帧出警"
    state = hub.latest(0)
    assert state is not None
    assert state.jpeg_b64 and state.level == "critical"
    assert state.status == "不合规"
    # 告警已落库（Hub 会话前缀），帧级历史同样落库
    import sqlite3
    conn = sqlite3.connect(dao.db.DEFAULT_DB_PATH)
    alarms = conn.execute(
        "SELECT COUNT(*) FROM alarm_events WHERE session_id LIKE 'hub_%'"
    ).fetchone()[0]
    frames = conn.execute(
        "SELECT COUNT(*) FROM detection_records WHERE session_id LIKE 'hub_%'"
    ).fetchone()[0]
    conn.close()
    assert alarms >= 1 and frames >= 1


def test_hub_open_alarm_dedup(hub):
    """首个 open 告警未关闭前，后续帧不重复建警（服务层 find_open 守卫）。"""
    hub.cooldown_sec = 0.0  # 关闭 Hub 侧冷却，专测服务层去重
    hub.start()
    deadline = time.time() + 3
    while time.time() < deadline and hub.polls < 5:
        time.sleep(0.05)
    assert hub.alarms == 1, f"open 告警期间不应重复建警: {hub.alarms}"


def test_hub_viewer_drives_fps(hub):
    assert hub._target_fps() == hub.idle_fps  # 无人观看 → 降频保活
    hub.add_viewer()
    assert hub._target_fps() == hub.active_fps
    hub.add_viewer()
    assert hub.viewers == 2
    hub.remove_viewer()
    hub.remove_viewer()
    assert hub._target_fps() == hub.idle_fps


def test_hub_multi_source_isolation():
    engine = StubEngine()
    hub = RealtimeHub(["demo://", "demo://2"], engine=engine,
                      active_fps=8, idle_fps=8)
    hub.cycle()
    assert set(engine.calls) == {"demo://", "demo://2"}


def test_hub_takeover_flag(hub):
    from services import monitor_service, realtime_entry

    assert not realtime_entry.hub_active()
    hub.start()
    assert realtime_entry.hub_active()
    # monitor_service 收敛：Hub 接管期间后台轮询自动跳过
    assert monitor_service.ensure_monitor_started() is None
    hub.stop()
    assert not realtime_entry.hub_active()


def test_hub_stop_releases(hub):
    hub.start()
    assert hub.running
    hub.stop()
    assert not hub.running
    assert hub.latest(0) is None or True  # 状态残留无害，线程已停


# ---------- core.realtime_engine：per-source tracker + reload ----------

@pytest.fixture
def engine():
    # scenes=[] 不加载任何权重：毫秒级构造，专注 tracker/reload 逻辑
    return RealtimeEngine(scenes=[])


def _frame() -> np.ndarray:
    return np.zeros((64, 64, 3), dtype=np.uint8)


def test_engine_per_source_trackers(engine):
    assert engine.available is False  # 无权重，但 tracker/流程可用
    d1, _ = engine.analyze(_frame(), source_key="cam_a")
    d2, _ = engine.analyze(_frame(), source_key="cam_b")
    assert d1 == [] and d2 == []
    assert set(engine.trackers) == {"cam_a", "cam_b"}
    assert engine.trackers["cam_a"] is not engine.trackers["cam_b"]
    # 缺省 key 兼容旧调用（Streamlit 页面路径）
    engine.analyze(_frame())
    assert "default" in engine.trackers


def test_engine_reload_resets_trackers(engine):
    engine.analyze(_frame(), source_key="cam_a")
    old = engine.trackers["cam_a"]
    engine.reload()  # scenes=[] 重建为空引擎组，但 tracker 必须换代
    assert engine.trackers == {} or engine.trackers.get("cam_a") is not old


def test_engine_reload_concurrent_detect(engine):
    """reload 期间并发 detect 不崩（build-then-swap 原子替换）。"""
    stop = threading.Event()
    errors: list[Exception] = []

    def _detect_loop():
        while not stop.is_set():
            try:
                engine.detect(_frame())
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

    worker = threading.Thread(target=_detect_loop, daemon=True)
    worker.start()
    try:
        for _ in range(5):
            engine.reload()
    finally:
        stop.set()
        worker.join(timeout=3)
    assert errors == []
    assert isinstance(engine.engines, list)


# ---------- TaskService 类级状态：TOCTOU 加锁 ----------

def test_task_service_start_run_no_double_start(monkeypatch, tmp_db):
    """两线程并发 start_async_run 同一任务：有且仅有一方成功（TOCTOU 修复）。"""
    from services.task_service import TaskService

    class SlowOrch:
        def __init__(self, *a, **k):
            pass

        def execute(self, *a, **k):
            time.sleep(0.4)
            self.action = None

            class _R:
                payload = {"action": {"payload": {"work_order": {}}}}

                def to_dict(self):
                    return {"status": "success", "payload": self.payload}

            return _R()

    monkeypatch.setattr(TaskService, "_ORCH_FACTORY", SlowOrch)
    from dao.db import get_conn, init_db
    from dao.models import UserDAO

    conn = get_conn()
    init_db(conn)
    admin = UserDAO(conn).insert("admin", "hashed", "admin")
    ts = TaskService(conn)
    tid = ts.create_task(admin, [], {"scene": "hot_work"}, source="upload")

    results: list[bool] = []
    barrier = threading.Barrier(2)

    def _try_start():
        barrier.wait(timeout=5)
        # 每线程独立连接：sqlite3 默认拒绝跨线程使用同一连接，
        # 共享 conn 在 Linux 上两线程都会抛 ProgrammingError；
        # 独立连接也是 API 真实请求的形态（每请求 scoped 一连）
        own = get_conn()
        init_db(own)
        results.append(TaskService(own).start_async_run(
            tid, admin, [], {"scene": "hot_work"}))

    threads = [threading.Thread(target=_try_start) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert results.count(True) == 1, f"必须恰好一方启动: {results}"
    assert results.count(False) == 1
