"""上传链路异步化测试（v0.6 二期c）：start_async_run 后台执行器。

monkeypatch Orchestrator 控制时长与失败注入；断言：重复启动被拒、
完成结果落 _async_results 且 work_orders 落库、异常路径转 failed。
"""
from __future__ import annotations

import time

import pytest

from dao.db import get_conn, init_db
from dao.models import UserDAO, WorkOrderDAO
from services import task_service as tsm
from services.task_service import TaskService


@pytest.fixture
def env(monkeypatch, tmp_path):
    # Phase 2 起 worker 内自开自关连接（修复跨线程闭库写），
    # 库必须落盘到临时文件，worker 的 scoped() 与测试断言连接指向同一物理库
    import dao.db as dao_db

    db_file = str(tmp_path / "async_run.db")
    monkeypatch.setattr(dao_db, "DEFAULT_DB_PATH", db_file)
    conn = get_conn(db_file)
    init_db(conn)
    users = UserDAO(conn)
    admin = users.insert("admin", "hashed", "admin")
    ts = TaskService(conn)
    tid = ts.create_task(admin, [], {"scene": "hot_work"}, source="upload")
    return {"conn": conn, "ts": ts, "admin": admin, "tid": tid,
            "monkeypatch": monkeypatch}


def _wait_result(ts, tid, timeout=15.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        res = ts.pop_async_result(tid)
        if res is not None:
            return res
        time.sleep(0.1)
    return None


def test_async_run_success_persists_work_order(env, monkeypatch):
    class FakeOrch:
        def __init__(self, *a, **k):
            pass

        def execute(self, task_id, images=None, permit_info=None):
            time.sleep(0.3)
            self.action = None   # 与真实 Orchestrator 接口对齐(润色桥判空跳过)
            class _R:
                payload = {"risk_level": "一般",
                           "vision": {"payload": {"detections": []}},
                           "rule": {"payload": {"compliance": []}},
                           "fusion": {"payload": {"risk_level": "一般",
                                                  "reasons": []}},
                           "review": {"payload": {"needs_review": False}},
                           "action": {"payload": {"work_order": {
                               "risk_level": "一般",
                               "hazard_desc": "异步样本",
                               "clause": "", "requirement": "整改"}}}}

            class _R2(_R):
                def to_dict(self):
                    return {"status": "success", "payload": self.payload}
            return _R2()

    monkeypatch.setattr(TaskService, "_ORCH_FACTORY", FakeOrch)
    assert env["ts"].start_async_run(
        env["tid"], env["admin"], [], {"scene": "hot_work"}) is True
    res = _wait_result(env["ts"], env["tid"])
    assert res is not None and res["status"] == "success"
    assert env["conn"].execute(
        "SELECT COUNT(*) FROM work_orders WHERE task_id=?",
        (env["tid"],)).fetchone()[0] == 1


def test_duplicate_start_rejected_while_running(env, monkeypatch):
    class SlowOrch:
        def __init__(self, *a, **k):
            pass

        def execute(self, *a, **k):
            time.sleep(1.2)

    monkeypatch.setattr(TaskService, "_ORCH_FACTORY", SlowOrch)
    assert env["ts"].start_async_run(
        env["tid"], env["admin"], [], {}) is True
    assert env["ts"].start_async_run(
        env["tid"], env["admin"], [], {}) is False     # 进行中拒绝重复
    _wait_result(env["ts"], env["tid"])                # 收尾清理


def test_async_failure_lands_readable_error(env, monkeypatch):
    class BoomOrch:
        def __init__(self, *a, **k):
            pass

        def execute(self, *a, **k):
            raise RuntimeError("模型目录损坏")

    monkeypatch.setattr(TaskService, "_ORCH_FACTORY", BoomOrch)
    env["ts"].start_async_run(env["tid"], env["admin"], [], {})
    res = _wait_result(env["ts"], env["tid"])
    assert res["status"] == "failed"
    assert "RuntimeError" in res["error"]
