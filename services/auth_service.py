"""认证与权限服务（M01）：登录校验、RBAC 权限、密码哈希。

职责：bcrypt 校验密码 → 写登录审计（仅 INSERT）；按 role 查 RBAC 矩阵。
"""
from __future__ import annotations

import bcrypt
import sqlite3

from dao.models import UserDAO, AuditDAO


class AuthService:
    """登录与权限服务。"""

    # RBAC 矩阵（LLD §2.2.1）：safety 受限，admin 全通
    _ROLE_ACTIONS: dict[str, set[str]] = {
        "admin": {"upload", "view", "export", "import_pdf", "view_all_logs", "override", "clear_data"},
        "safety": {"upload", "view", "export", "override"},
    }

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.users = UserDAO(conn)
        self.audit = AuditDAO(conn)

    def hash_password(self, pwd: str) -> str:
        """返回 bcrypt 哈希串。"""
        return bcrypt.hashpw(pwd.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    def login(self, username: str, password: str) -> dict:
        """登录：成功返回 {ok, role, user_id} 并写审计；失败返回 {ok:False} 并写审计。"""
        row = self.users.get_by_name(username)
        if row is None:
            self.audit.insert(None, "login_fail", f'{{"username":"{username}"}}')
            return {"ok": False, "error": "用户不存在"}
        user_id = row["id"]
        stored_hash = row["pwd_hash"].encode("utf-8") if isinstance(row["pwd_hash"], str) else row["pwd_hash"]
        try:
            ok = bcrypt.checkpw(password.encode("utf-8"), stored_hash)
        except (ValueError, TypeError):
            ok = False
        if ok:
            self.audit.insert(user_id, "login", '{"detail":"登录成功"}')
            return {"ok": True, "role": row["role"], "user_id": user_id}
        self.audit.insert(user_id, "login_fail", '{"detail":"密码错误"}')
        return {"ok": False, "error": "密码错误"}

    def check_permission(self, user_id: str, action: str) -> bool:
        """按用户角色判定是否允许某操作。"""
        row = self.users.get_by_name(self._username_by_id(user_id))
        if row is None:
            return False
        allowed = self._ROLE_ACTIONS.get(row["role"], set())
        return action in allowed

    def _username_by_id(self, user_id: str) -> str | None:
        cur = self.conn.execute("SELECT username FROM users WHERE id=?", (user_id,)).fetchone()
        return cur["username"] if cur else None
