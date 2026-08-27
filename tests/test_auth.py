"""认证服务测试（TDD：登录成功/失败、RBAC、密码哈希）。"""

import bcrypt
import json

import pytest
from dao.db import get_conn, init_db
from dao.models import UserDAO
from services.auth_service import AuthService, _FAIL_LIMIT


@pytest.fixture
def auth():
    conn = get_conn(":memory:")
    init_db(conn)
    UserDAO(conn).insert("alice", "hashed", "safety")
    # 用真实哈希覆盖密码为 known
    import bcrypt
    h = bcrypt.hashpw(b"secret123", bcrypt.gensalt()).decode()
    conn.execute("UPDATE users SET pwd_hash=? WHERE username=?", (h, "alice"))
    conn.commit()
    return AuthService(conn)


def test_login_success(auth):
    r = auth.login("alice", "secret123")
    assert r["ok"] is True
    assert r["role"] == "safety"
    assert "user_id" in r


def test_login_wrong_pwd(auth):
    r = auth.login("alice", "bad")
    assert r.get("ok") is False


def test_login_unknown_user(auth):
    r = auth.login("nobody", "x")
    assert r["ok"] is False


def test_check_permission_safety_limited(auth):
    uid = auth.login("alice", "secret123")["user_id"]
    assert auth.check_permission(uid, "upload") is True
    assert auth.check_permission(uid, "import_pdf") is False
    assert auth.check_permission(uid, "clear_data") is False


def test_admin_clear_data_permission():
    conn = get_conn(":memory:")
    init_db(conn)
    uid = UserDAO(conn).insert("root", "hashed", "admin")
    assert AuthService(conn).check_permission(uid, "clear_data") is True


def test_hash_password_roundtrip():
    svc = AuthService(get_conn(":memory:"))
    h = svc.hash_password("abc")
    assert h != "abc"
    assert h.startswith("$2")


def test_login_fail_audit_json_is_valid(auth):
    """含引号的用户名不得破坏审计 detail_json（注入回归）。"""
    r = auth.login('inj"tional', "x")
    assert r["ok"] is False
    row = auth.conn.execute(
        "SELECT detail_json FROM audit_logs WHERE action='login_fail' "
        "ORDER BY rowid DESC LIMIT 1").fetchone()
    data = json.loads(row["detail_json"])
    assert data["username"] == 'inj"tional'


def test_login_locked_after_repeated_failures():
    """滑动窗口内连续失败达上限后临时锁定：正确密码也拒绝并写审计。"""
    conn = get_conn(":memory:")
    init_db(conn)
    h = bcrypt.hashpw(b"secret123", bcrypt.gensalt()).decode()
    UserDAO(conn).insert("lockme", h, "safety")
    svc = AuthService(conn)
    for _ in range(_FAIL_LIMIT):
        assert svc.login("lockme", "wrong")["ok"] is False
    locked = svc.login("lockme", "secret123")
    assert locked["ok"] is False
    assert "过多" in locked["error"]
    row = conn.execute(
        "SELECT detail_json FROM audit_logs WHERE action='login_fail' "
        "AND detail_json LIKE '%锁定%' LIMIT 1").fetchone()
    assert row is not None


def test_login_success_resets_failure_counter():
    """失败未达上限时登录成功，计数清零，后续仍可正常尝试。"""
    conn = get_conn(":memory:")
    init_db(conn)
    h = bcrypt.hashpw(b"secret123", bcrypt.gensalt()).decode()
    UserDAO(conn).insert("resetme", h, "safety")
    svc = AuthService(conn)
    for _ in range(_FAIL_LIMIT - 1):
        assert svc.login("resetme", "wrong")["ok"] is False
    assert svc.login("resetme", "secret123")["ok"] is True
    assert svc.login("resetme", "wrong")["ok"] is False
