"""v0.8 安全收口与账号治理测试。

覆盖：文件名消毒（路径穿越回归）、配置 ${ENV} 展开、账号治理
（建用户/改密/重置/停用/最后管理员守卫）、派发即推送、审计导出与
受控留存（purge 凭证 + 触发器重建）、异步进度按属主隔离、RTSP 凭据打码。
"""
from __future__ import annotations

import json
import os
import sqlite3

import pytest

from core.config import ConfigLoader
from core.evidence import sanitize_filename
from core.video_source import mask_source
from dao.db import get_conn, init_db
from dao.models import UserDAO, WorkOrderDAO
from scripts.audit_maintenance import purge
from services.audit_service import AuditService
from services.auth_service import AuthService
from services.dispatch_service import DispatchService
from services.task_service import TaskService


@pytest.fixture
def env():
    conn = get_conn(":memory:")
    init_db(conn)
    svc = AuthService(conn)
    # 引导管理员经 DAO 直建（create_user 的权限门要求已有管理员）
    admin_id = UserDAO(conn).insert("root", svc.hash_password("rootpwd123"),
                                    "admin")
    return {"conn": conn, "svc": svc, "admin_id": admin_id}


# ---------- 文件名消毒 ----------

def test_sanitize_filename_blocks_traversal():
    for evil in ("../../etc/passwd", "a/../../b.png"):
        out = sanitize_filename(evil)
        assert "/" not in out and "\\" not in out


def test_sanitize_filename_blocks_absolute_and_dotdot():
    for evil in ("/etc/cron.d/evil", "C:\\Users\\x\\evil.pdf",
                 "..\\..\\evil", "..", ".", "", None):
        out = sanitize_filename(evil)
        assert "/" not in out and "\\" not in out
        assert not out.startswith(".")
        assert ":" not in out


def test_sanitize_filename_keeps_cjk_and_ext():
    assert sanitize_filename("规范文档 GB50016.pdf") == "规范文档_GB50016.pdf"
    assert sanitize_filename(None, fallback="spec.pdf") == "spec.pdf"


def test_sanitize_filename_caps_length():
    assert len(sanitize_filename("x" * 500)) <= 64


# ---------- 配置 ${ENV} 展开 ----------

def test_config_env_expansion(monkeypatch, tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "asr:\n  api_key: ${TEST_ASR_KEY:-fallback_key}\n"
        "notify:\n  webhook_url: ${TEST_WEBHOOK}\n"
        "models:\n  yolo_onnx: data/models/x.onnx\n",
        encoding="utf-8")
    monkeypatch.setenv("TEST_WEBHOOK", "https://example.com/hook")
    monkeypatch.delenv("TEST_ASR_KEY", raising=False)
    loader = ConfigLoader(str(cfg))
    data = loader.load()
    assert data["asr"]["api_key"] == "fallback_key"          # 默认值生效
    assert data["notify"]["webhook_url"] == "https://example.com/hook"
    assert data["models"]["yolo_onnx"] == "data/models/x.onnx"  # 非字符串不动


def test_config_env_unset_placeholder_kept(monkeypatch, tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("notify:\n  webhook_url: ${TEST_MISSING_VAR}\n",
                   encoding="utf-8")
    monkeypatch.delenv("TEST_MISSING_VAR", raising=False)
    assert ConfigLoader(str(cfg)).load()["notify"]["webhook_url"] == \
        "${TEST_MISSING_VAR}"


# ---------- 账号治理 ----------

def test_create_user_and_login_with_flag(env):
    res = env["svc"].create_user(env["admin_id"], "wangwu", "wangwu888",
                                 "responsible")
    assert res["ok"] is True
    login = env["svc"].login("wangwu", "wangwu888")
    assert login["ok"] is True
    assert login["must_change_password"] is True       # 初始密码标记
    assert login["role"] == "responsible"


def test_create_user_rejects_non_admin(env):
    env["svc"].create_user(env["admin_id"], "s1", "s1pwd12345", "safety")
    safety_id = env["conn"].execute(
        "SELECT id FROM users WHERE username='s1'").fetchone()["id"]
    res = env["svc"].create_user(safety_id, "s2", "s2pwd12345", "safety")
    assert res["ok"] is False and "无权限" in res["error"]


def test_create_user_validation(env):
    assert env["svc"].create_user(env["admin_id"], "x", "longenough1",
                                  "safety")["ok"] is False       # 用户名过短
    assert env["svc"].create_user(env["admin_id"], "okname", "短",
                                  "safety")["ok"] is False       # 密码过短
    assert env["svc"].create_user(env["admin_id"], "okname", "longenough1",
                                  "boss")["ok"] is False         # 角色非法
    assert env["svc"].create_user(env["admin_id"], "root", "longenough1",
                                  "safety")["ok"] is False       # 重名


def test_change_password_flow(env):
    env["svc"].create_user(env["admin_id"], "zhaoliu", "zhaoliu666",
                           "responsible")
    uid = env["conn"].execute(
        "SELECT id FROM users WHERE username='zhaoliu'").fetchone()["id"]
    assert env["svc"].change_password(uid, "wrong-old", "newpwd12345")[
        "ok"] is False
    assert env["svc"].change_password(uid, "zhaoliu666", "short")[
        "ok"] is False
    res = env["svc"].change_password(uid, "zhaoliu666", "newpwd12345")
    assert res["ok"] is True
    login = env["svc"].login("zhaoliu", "newpwd12345")
    assert login["ok"] is True
    assert login["must_change_password"] is False      # 改密即清标记


def test_admin_reset_forces_change(env):
    env["svc"].create_user(env["admin_id"], "sunqi", "sunqi77777",
                           "responsible")
    uid = env["conn"].execute(
        "SELECT id FROM users WHERE username='sunqi'").fetchone()["id"]
    env["svc"].change_password(uid, "sunqi77777", "selfpwd12345")
    assert env["svc"].admin_reset_password(env["admin_id"], uid,
                                           "resetpwd123")["ok"] is True
    login = env["svc"].login("sunqi", "resetpwd123")
    assert login["must_change_password"] is True       # 重置后重新要求改密


def test_disable_blocks_login_and_permission(env):
    env["svc"].create_user(env["admin_id"], "zhouba", "zhouba8888",
                           "safety")
    uid = env["conn"].execute(
        "SELECT id FROM users WHERE username='zhouba'").fetchone()["id"]
    assert env["svc"].check_permission(uid, "upload") is True
    assert env["svc"].set_user_disabled(env["admin_id"], uid, True)["ok"]
    assert env["svc"].login("zhouba", "zhouba8888")["ok"] is False
    assert "停用" in env["svc"].login("zhouba", "zhouba8888")["error"]
    assert env["svc"].check_permission(uid, "upload") is False   # 即时生效
    assert env["svc"].set_user_disabled(env["admin_id"], uid, False)["ok"]
    assert env["svc"].login("zhouba", "zhouba8888")["ok"] is True


def test_disable_guards(env):
    # 不能停用自己（actor 与目标相同即拒绝）
    assert env["svc"].set_user_disabled(env["admin_id"], env["admin_id"],
                                        True)["ok"] is False
    # 正常停用他人后可再启用
    env["svc"].create_user(env["admin_id"], "root2", "root2pwd123", "admin")
    root2_id = env["conn"].execute(
        "SELECT id FROM users WHERE username='root2'").fetchone()["id"]
    assert env["svc"].set_user_disabled(env["admin_id"], root2_id,
                                        True)["ok"] is True
    assert env["svc"].set_user_disabled(env["admin_id"], root2_id,
                                        False)["ok"] is True
    # 停用目标不存在时报错而非抛异常
    assert env["svc"].set_user_disabled(env["admin_id"], "u_ghost",
                                        True)["ok"] is False


# ---------- 派发即推送 ----------

class FakeNotifier:
    def __init__(self):
        self.calls = []

    def push_dispatch(self, order_id, assignee, hazard, deadline, risk_level):
        self.calls.append((order_id, assignee, deadline, risk_level))
        return {"ok": True, "status": "sent"}


def test_dispatch_pushes_assignee(env):
    env["svc"].create_user(env["admin_id"], "lisi", "lisipwd888",
                           "responsible", must_change_password=False)
    lisi_id = env["conn"].execute(
        "SELECT id FROM users WHERE username='lisi'").fetchone()["id"]
    task_id = "t_testpush1"
    env["conn"].execute(
        "INSERT INTO tasks(id,user_id,permit_json,status,created_at) "
        "VALUES(?,?,?,?,datetime('now'))",
        (task_id, env["admin_id"], "{}", "completed"))
    env["conn"].commit()
    WorkOrderDAO(env["conn"]).insert(
        task_id=task_id, hazard_desc="动火区有火花", clause="第X条",
        requirement="清理", risk_level="较大", worker_notice="注意")
    notifier = FakeNotifier()
    dsp = DispatchService(env["conn"], rules=[{"assignee": "lisi"}],
                          notifier=notifier)
    oid = dsp.dispatch_order(task_id, env["admin_id"], deadline_hours=2)
    assert notifier.calls and notifier.calls[0][0] == oid
    assert notifier.calls[0][1] == "lisi"
    assert notifier.calls[0][3] == "较大"
    # 审计留痕：dispatch + dispatch 通知管线
    actions = {r["action"] for r in env["conn"].execute(
        "SELECT action FROM audit_logs").fetchall()}
    assert "dispatch" in actions


def test_dispatch_without_notifier_still_works(env, monkeypatch):
    """真实路径（daemon 线程）在 notify 未启用时静默 skipped，不影响派发。"""
    env["svc"].create_user(env["admin_id"], "lisi", "lisipwd888",
                           "responsible", must_change_password=False)
    task_id = "t_testpush2"
    env["conn"].execute(
        "INSERT INTO tasks(id,user_id,permit_json,status,created_at) "
        "VALUES(?,?,?,?,datetime('now'))",
        (task_id, env["admin_id"], "{}", "completed"))
    env["conn"].commit()
    WorkOrderDAO(env["conn"]).insert(
        task_id=task_id, hazard_desc="烟雾", clause="", requirement="处理",
        risk_level="较大", worker_notice="")
    dsp = DispatchService(env["conn"], rules=[{"assignee": "lisi"}])
    oid = dsp.dispatch_order(task_id, env["admin_id"], deadline_hours=1)
    assert oid


# ---------- 审计导出与受控留存 ----------

def test_audit_export_csv(env):
    AuditService(env["conn"]).append(env["admin_id"], "unit_test",
                                     {"k": "v,含逗号"})
    text, n = AuditService(env["conn"]).export_csv()
    assert n >= 1 and "unit_test" in text
    # 区间过滤：历史区间为空
    text0, n_empty = AuditService(env["conn"]).export_csv(
        start="1999-01-01", end="1999-12-31")
    assert n_empty == 0


def _insert_old_audit(conn, action: str) -> None:
    """插入一条旧时间戳审计行（INSERT 不触发仅追加约束，UPDATE/DELETE 才会）。"""
    conn.execute(
        "INSERT INTO audit_logs(user_id, action, detail_json, created_at) "
        "VALUES(?, ?, '{}', '2000-01-01 00:00:00')", (None, action))
    conn.commit()


def test_audit_purge_export_only_preserves_rows(env, tmp_path):
    _insert_old_audit(env["conn"], "old_action")
    res = purge(env["conn"], tmp_path, "2001-01-01", delete=False)
    assert res["exported"] == 1 and res["deleted"] == 0
    assert os.path.exists(res["archive_file"])
    cnt = env["conn"].execute(
        "SELECT COUNT(*) FROM audit_logs WHERE action='old_action'"
    ).fetchone()[0]
    assert cnt == 1                                  # 仅追加语义未破


def test_audit_purge_delete_with_credential_and_trigger(env, tmp_path):
    _insert_old_audit(env["conn"], "very_old")
    res = purge(env["conn"], tmp_path, "2001-01-01", delete=True)
    assert res["exported"] == 1 and res["deleted"] == 1
    # purge 凭证已留痕（audit_archive，新时间戳不在删除范围内）
    cred = env["conn"].execute(
        "SELECT detail_json FROM audit_logs WHERE action='audit_archive'"
    ).fetchone()
    assert cred is not None and json.loads(cred["detail_json"])["rows"] == 1
    # 禁删触发器已原样重建：插入一条新审计行后 DELETE 仍被拒绝
    assert AuditService(env["conn"]).append(
        env["admin_id"], "post_purge", {})["ok"]
    with pytest.raises(sqlite3.DatabaseError):
        env["conn"].execute("DELETE FROM audit_logs")


# ---------- 异步进度按属主隔离 ----------

def test_progress_isolated_by_owner(env):
    ts = TaskService(env["conn"])
    tid = ts.create_task(env["admin_id"], [], {"scene": "hot_work"})
    ts.update_progress(tid, "vision", "running", 10)
    assert ts.get_progress(tid, env["admin_id"]).get("vision")
    stranger = env["svc"].create_user(env["admin_id"], "stranger",
                                      "strangepwd8", "safety")
    stranger_id = env["conn"].execute(
        "SELECT id FROM users WHERE username='stranger'").fetchone()["id"]
    assert stranger["ok"]
    assert ts.get_progress(tid, stranger_id) == {}   # 非属主看不到
    assert ts.pop_async_result(tid, stranger_id) is None
    assert ts.start_async_run(tid, stranger_id, [], {}) is False  # 不能替跑
    # 未传 user_id 保持旧行为（内部/兼容调用）
    assert ts.get_progress(tid) != {}


# ---------- RTSP 凭据打码 ----------

def test_mask_source_hides_password():
    assert mask_source("rtsp://admin:secrets@192.168.1.10:554/stream") == \
        "rtsp://admin:****@192.168.1.10:554/stream"


def test_mask_source_keeps_plain_paths():
    assert mask_source("D:/videos/cam1.mp4") == "D:/videos/cam1.mp4"
    assert mask_source("demo://") == "demo://"
    assert mask_source("rtsp://192.168.1.10/stream") == \
        "rtsp://192.168.1.10/stream"                 # 无凭据不动


# ---------- AI 通道连通性自检（v0.8）----------

class _FakeResp:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b'{"choices":[{"message":{"content":"ok"}}]}'


def _cloud_engine():
    from services.enhance_service import EnhanceEngine
    eng = EnhanceEngine()
    # 直接注入 providers，测试不依赖本机 config 是否配置了真实 key
    eng.providers = [{"name": "test-cloud", "type": "cloud",
                      "api_base": "https://api.example.com/v1",
                      "api_key": "test-key", "model": "test-model",
                      "timeout_sec": 20}]
    return eng


def test_check_provider_cloud_ok(monkeypatch):
    import urllib.request
    eng = _cloud_engine()
    captured = {}

    def fake(req, timeout=None):
        captured["url"] = req.full_url
        captured["auth"] = req.headers.get("Authorization")
        return _FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    r = eng.check_provider(eng.providers[0])
    assert r["ok"] is True and r["status"] == "ok"
    assert "test-model" in r["detail"]
    assert "/chat/completions" in captured["url"]
    assert captured["auth"] == "Bearer test-key"


def test_check_provider_cloud_bad_key(monkeypatch):
    import urllib.error
    import urllib.request
    eng = _cloud_engine()

    def fake(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized",
                                     None, None)

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    r = eng.check_provider(eng.providers[0])
    assert r["ok"] is False and "key 无效" in r["detail"]


def test_check_provider_cloud_model_404(monkeypatch):
    import urllib.error
    import urllib.request
    eng = _cloud_engine()

    def fake(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found",
                                     None, None)

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    r = eng.check_provider(eng.providers[0])
    assert r["ok"] is False and "404" in r["detail"] and "test-model" in r["detail"]


def test_check_provider_cloud_unreachable(monkeypatch):
    import urllib.error
    import urllib.request
    eng = _cloud_engine()

    def fake(req, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    r = eng.check_provider(eng.providers[0])
    assert r["ok"] is False and "不可达" in r["detail"]


def test_check_cloud_compat_without_cloud():
    from services.enhance_service import EnhanceEngine
    eng = EnhanceEngine()
    eng.providers = []
    r = eng.check_cloud()
    assert r["status"] == "unconfigured" and r["ok"] is False


def _asr_engine():
    from core.asr_engine import AsrEngine
    eng = AsrEngine()
    eng.enabled = True
    eng.api_base = "https://api.example.com/v1"
    eng.api_key = "test-key"
    eng.model = "whisper-1"
    return eng


def test_check_asr_ok(monkeypatch):
    import urllib.request
    from core import asr_engine as ae
    eng = _asr_engine()
    captured = {}

    def fake(req, timeout=None):
        captured["url"] = req.full_url
        captured["body"] = req.data
        return _FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    r = eng.check_connectivity()
    assert r["ok"] is True and "端到端" in r["detail"]
    assert "/audio/transcriptions" in captured["url"]
    assert b"check.wav" in captured["body"]          # multipart 真实带音频段


def test_check_asr_bad_key(monkeypatch):
    import urllib.error
    import urllib.request
    eng = _asr_engine()

    def fake(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden",
                                     None, None)

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    r = eng.check_connectivity()
    assert r["ok"] is False and "key 无效" in r["detail"]


def test_check_asr_short_audio_hint(monkeypatch):
    """部分兼容服务对静音/极短音频 400：给出可读提示而非笼统失败。"""
    import urllib.error
    import urllib.request
    eng = _asr_engine()

    def fake(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 400, "Bad Request",
                                     None, None)

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    r = eng.check_connectivity()
    assert r["ok"] is False and "400" in r["detail"] and "鉴权" in r["detail"]


def test_check_asr_unconfigured():
    from core.asr_engine import AsrEngine
    eng = AsrEngine()
    eng.enabled = False
    r = eng.check_connectivity()
    assert r["status"] == "unconfigured" and r["ok"] is False


def test_diag_build_checks_ai_rows():
    """每个云 provider 一行检查（v0.8 多 base）；未配置的通道不渲染（静默约定）。"""
    import ui.page_diag as diag
    keys = [k for k, _, _ in diag._build_checks(
        [], None, ai_channels={"llm_cloud": ["deepseek"], "asr": False})]
    assert "llm_deepseek" in keys and "asr_cloud" not in keys
    assert "llm_local" in keys
    keys2 = [k for k, _, _ in diag._build_checks(
        [], None, ai_channels={"llm_cloud": ["deepseek", "openai"], "asr": True})]
    assert "llm_deepseek" in keys2 and "llm_openai" in keys2
    assert "asr_cloud" in keys2


def test_diag_llm_provider_row_fn(monkeypatch):
    """自检行闭包按 name 定位 provider 并返回可读结果。"""
    import urllib.error
    import urllib.request
    import ui.page_diag as diag
    eng = _cloud_engine()
    monkeypatch.setattr("services.enhance_service.EnhanceEngine",
                        lambda: eng)

    def fake(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized",
                                     None, None)

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    ok, detail = diag._make_llm_provider_check("test-cloud")()
    assert ok is False and "key 无效" in detail
    # 未知名/未配置 → 绿色跳过而非误报
    ok2, _ = diag._make_llm_provider_check("ghost")()
    assert ok2 is True
