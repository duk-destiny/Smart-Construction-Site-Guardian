"""认证服务测试（TDD：登录成功/失败、RBAC、密码哈希）。"""

import pytest
from dao.db import get_conn, init_db
from dao.models import UserDAO
from services.auth_service import AuthService


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


def test_hash_password_roundtrip():
    svc = AuthService(get_conn(":memory:"))
    h = svc.hash_password("abc")
    assert h != "abc"
    assert h.startswith("$2")
