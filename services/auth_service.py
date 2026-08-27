"""认证与权限服务（M01）：登录校验、RBAC 权限、密码哈希。

职责：bcrypt 校验密码 → 写登录审计（仅 INSERT）；按 role 查 RBAC 矩阵。
审计 detail 一律经 json.dumps 构造（禁止 f-string 拼接，防审计记录注入）；
登录失败按用户名做进程内滑动窗口限速，达上限临时锁定，缓解暴力破解。
"""
from __future__ import annotations

import bcrypt
import json
import sqlite3
import time

from dao.models import UserDAO, AuditDAO

# 登录失败限速：同一用户名在滑动窗口内失败达上限即临时锁定（仅内存态）
_FAIL_WINDOW_SEC = 300.0
_FAIL_LIMIT = 10
_FAILS: dict[str, list[float]] = {}


def _detail_json(detail: dict) -> str:
    """审计 detail 统一经 json 序列化，含引号/特殊字符的输入不会破坏 JSON。"""
    return json.dumps(detail, ensure_ascii=False)


class AuthService:
    """登录与权限服务。"""

    # RBAC 矩阵（LLD §2.2.1）：safety 受限；admin 全通；
    # responsible（v0.2 整改责任人）仅可查看与提交整改，不进管理端
    _ROLE_ACTIONS: dict[str, set[str]] = {
        "admin": {"upload", "view", "export", "import_pdf", "view_all_logs", "override", "clear_data"},
        "safety": {"upload", "view", "export", "override"},
        "responsible": {"view", "rectify"},
    }

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.users = UserDAO(conn)
        self.audit = AuditDAO(conn)

    def hash_password(self, pwd: str) -> str:
        """返回 bcrypt 哈希串。"""
        return bcrypt.hashpw(pwd.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    def login(self, username: str, password: str) -> dict:
        """登录：成功返回 {ok, role, user_id} 并写审计；失败返回 {ok:False} 并写审计。

        同一用户名在 _FAIL_WINDOW_SEC 内连续失败 _FAIL_LIMIT 次后临时锁定：
        直接拒绝并写 login_fail 审计（不查库），登录成功清零计数。
        """
        now = time.monotonic()
        fails = [t for t in _FAILS.get(username, []) if now - t < _FAIL_WINDOW_SEC]
        _FAILS[username] = fails
        if len(fails) >= _FAIL_LIMIT:
            self.audit.insert(None, "login_fail", _detail_json({
                "username": username, "detail": "失败次数过多，临时锁定"}))
            return {"ok": False,
                    "error": f"登录失败次数过多，请约{int(_FAIL_WINDOW_SEC // 60)}分钟后再试"}

        row = self.users.get_by_name(username)
        if row is None:
            fails.append(now)
            self.audit.insert(None, "login_fail",
                              _detail_json({"username": username}))
            return {"ok": False, "error": "用户不存在"}
        user_id = row["id"]
        stored_hash = row["pwd_hash"].encode("utf-8") if isinstance(row["pwd_hash"], str) else row["pwd_hash"]
        try:
            ok = bcrypt.checkpw(password.encode("utf-8"), stored_hash)
        except (ValueError, TypeError):
            ok = False
        if ok:
            _FAILS.pop(username, None)
            self.audit.insert(user_id, "login", '{"detail":"登录成功"}')
            return {"ok": True, "role": row["role"], "user_id": user_id}
        fails.append(now)
        self.audit.insert(user_id, "login_fail",
                          _detail_json({"detail": "密码错误"}))
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
