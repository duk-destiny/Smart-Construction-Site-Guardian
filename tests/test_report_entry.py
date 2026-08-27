"""v0.4 上报扩展测试：ASR 静默客户端 + 文字线索建单（source=text）。

覆盖：未配置即静默、multipart 纯函数结构、转写网络失败返回 None；
文字建单的严重度白名单校验、critical→较大 映射、位置前缀、
审计 text_report、safe 正向信号拒绝。
"""
from __future__ import annotations

import pytest

from core.asr_engine import AsrEngine
from dao.db import get_conn, init_db
from dao.models import AuditDAO, UserDAO, WorkOrderDAO
from services.task_service import TaskService
from services.permission_service import PermissionError


@pytest.fixture
def env():
    conn = get_conn(":memory:")
    init_db(conn)
    users = UserDAO(conn)
    admin = users.insert("admin", "hashed", "admin")
    safety = users.insert("zhangsan", "hashed", "safety")
    return {"conn": conn, "svc": TaskService(conn),
            "ids": {"admin": admin, "safety": safety}}


# ---------- AsrEngine ----------

def test_asr_silent_when_unconfigured():
    # 默认 config.yaml 中 asr.enabled=false → available 必须为 False（静默约定）
    assert AsrEngine().available() is False


def test_multipart_structure_pure_function():
    body, ctype = AsrEngine.build_multipart(
        b"BIN\ry\n", "rec 1.wav", {"model": "whisper-1", "language": "zh"})
    assert b'name="model"' in body and b"whisper-1" in body
    assert b'filename="rec 1.wav"' in body and b"BIN\ry\n" in body
    assert ctype.startswith("multipart/form-data; boundary=")
    assert body.rstrip().endswith(b"--")


def test_transcribe_network_fail_returns_none_and_records(monkeypatch):
    eng = AsrEngine(api_base="https://x/v1", api_key="k", model="whisper-1")
    # 强制视为已配置，只测失败路径
    monkeypatch.setattr(eng, "available", lambda: True)

    def boom(*a, **k):
        raise OSError("reset")
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert eng.transcribe(b"x") is None
    assert "OSError" in (eng.last_error or "")


# ---------- create_text_hazard ----------

def test_text_report_happy_path(env):
    tid = env["svc"].create_text_hazard(
        env["ids"]["safety"], "西侧堆放纸箱无人清理", "flammable",
        scene_id="hot_work", location="3号楼西侧")
    task = env["svc"].tasks.get(tid)
    assert task["source"] == "text"
    risk = env["svc"].risks.get_by_task(tid)
    assert risk["risk_level"] == "一般"           # warning→一般
    wo = env["svc"].work_orders.get_by_task(tid)
    assert "[3号楼西侧]" in wo["hazard_desc"]
    assert wo["status"] == "open"
    actions = [r["action"] for r in
               env["conn"].execute("SELECT action FROM audit_logs").fetchall()]
    assert "text_report" in actions


def test_critical_maps_to_higher_level(env):
    tid = env["svc"].create_text_hazard(
        env["ids"]["safety"], "有人未戴安全帽作业", "no_helmet")
    assert env["svc"].risks.get_by_task(tid)["risk_level"] == "较大"


def test_safe_signal_rejected(env):
    with pytest.raises(ValueError, match="正向安全信号"):
        env["svc"].create_text_hazard(env["ids"]["safety"], "都戴了帽子",
                                      "helmet")


def test_unknown_key_rejected(env):
    with pytest.raises(ValueError, match="白名单"):
        env["svc"].create_text_hazard(env["ids"]["safety"], "乱写", "ufo")


def test_responsible_cannot_create_text_report(env):
    users = UserDAO(env["conn"])
    lisi = users.insert("lisi", "hashed", "responsible")
    with pytest.raises(PermissionError):
        env["svc"].create_text_hazard(lisi, "描述", "spark")


def test_audit_contains_scene_and_cls(env):
    env["svc"].create_text_hazard(env["ids"]["safety"], "描述", "smoke",
                                  scene_id="hot_work")
    rows = [dict(r) for r in env["conn"].execute(
        "SELECT action, detail_json FROM audit_logs "
        "WHERE action='text_report'").fetchall()]
    assert len(rows) == 1
    import json
    detail = json.loads(rows[0]["detail_json"])
    assert detail["cls"] == "smoke" and detail["scene"] == "hot_work"
