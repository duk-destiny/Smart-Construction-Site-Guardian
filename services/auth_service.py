"""认证与账号治理服务（M01）：登录校验、RBAC 权限、密码哈希、用户管理。

职责：bcrypt 校验密码 → 写登录审计（仅 INSERT）；按 role 查 RBAC 矩阵。
审计 detail 一律经 json.dumps 构造（禁止 f-string 拼接，防审计记录注入）；
登录失败按用户名做进程内滑动窗口限速，达上限临时锁定，缓解暴力破解；
_FAILS 字典有容量上限（FIFO 淘汰），防任意用户名灌爆进程内存（v0.8）。
v0.8 账号治理：建用户 / 改密 / 管理员重置 / 停用启用，全部写审计；
停用在登录与 check_permission 双侧生效（活跃会话的下一次鉴权即被拒）。
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
# 限速字典容量上限：极端情况下被未知用户名灌入时 FIFO 淘汰最旧条目
_FAILS_MAX_USERS = 10_000

# 账号治理入参约束（v0.8）
_MIN_PWD_LEN = 8
_MIN_NAME_LEN = 2
_MAX_NAME_LEN = 32
_ROLES = ("safety", "admin", "responsible")


def _detail_json(detail: dict) -> str:
    """审计 detail 统一经 json 序列化，含引号/特殊字符的输入不会破坏 JSON。"""
    return json.dumps(detail, ensure_ascii=False)


class AuthService:
    """登录、权限与账号治理服务。"""

    # RBAC 矩阵（LLD §2.2.1）：safety 受限；admin 全通；
    # responsible（v0.2 整改责任人）仅可查看与提交整改，不进管理端。
    # v0.8：manage_users 为管理端账号治理专属动作（建用户/重置/停用）。
    _ROLE_ACTIONS: dict[str, set[str]] = {
        "admin": {"upload", "view", "export", "import_pdf", "view_all_logs",
                  "override", "clear_data", "manage_users"},
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
        """登录：成功返回 {ok, role, user_id, must_change_password} 并写审计。

        同一用户名在 _FAIL_WINDOW_SEC 内连续失败 _FAIL_LIMIT 次后临时锁定：
        直接拒绝并写 login_fail 审计（不查库），登录成功清零计数。
        停用账号（disabled=1）直接拒绝；初始密码未改时带
        must_change_password=True 供 UI 层做首登改密门控（v0.8）。
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
        if row["disabled"]:
            self.audit.insert(row["id"], "login_fail",
                              _detail_json({"detail": "账号已停用"}))
            return {"ok": False, "error": "账号已停用，请联系管理员"}
        user_id = row["id"]
        stored_hash = row["pwd_hash"].encode("utf-8") if isinstance(row["pwd_hash"], str) else row["pwd_hash"]
        try:
            ok = bcrypt.checkpw(password.encode("utf-8"), stored_hash)
        except (ValueError, TypeError):
            ok = False
        if ok:
            _FAILS.pop(username, None)
            self.audit.insert(user_id, "login", '{"detail":"登录成功"}')
            return {"ok": True, "role": row["role"], "user_id": user_id,
                    "must_change_password": bool(row["must_change_password"])}
        fails.append(now)
        self.audit.insert(user_id, "login_fail",
                          _detail_json({"detail": "密码错误"}))
        return {"ok": False, "error": "密码错误"}

    def check_permission(self, user_id: str, action: str) -> bool:
        """按用户角色判定是否允许某操作；停用账号一律拒绝（立即生效）。"""
        row = self.users.get_by_name(self._username_by_id(user_id))
        if row is None or row["disabled"]:
            return False
        allowed = self._ROLE_ACTIONS.get(row["role"], set())
        return action in allowed

    def _username_by_id(self, user_id: str) -> str | None:
        cur = self.conn.execute("SELECT username FROM users WHERE id=?", (user_id,)).fetchone()
        return cur["username"] if cur else None

    # ---------- v0.8 账号治理 ----------
    @staticmethod
    def _validate_credentials(username: str, password: str) -> str | None:
        """建用户/改密入参校验，返回错误消息或 None。"""
        name = (username or "").strip()
        if not (_MIN_NAME_LEN <= len(name) <= _MAX_NAME_LEN):
            return f"用户名需 {_MIN_NAME_LEN}-{_MAX_NAME_LEN} 个字符"
        if len(password or "") < _MIN_PWD_LEN:
            return f"密码至少 {_MIN_PWD_LEN} 位"
        return None

    def create_user(self, actor_user_id: str | None, username: str,
                    password: str, role: str,
                    must_change_password: bool = True) -> dict:
        """管理员建用户：校验入参 → 唯一性 → 落库 → 审计。

        返回 {ok, data:{user_id}} 或 {ok:False, error}。新账号默认带
        must_change_password=1（首登改密门控），演示批量建号可显式关闭。
        """
        try:
            self._require_actor(actor_user_id, "manage_users")
        except PermissionError as exc:
            return {"ok": False, "error": str(exc)}
        role = (role or "").strip()
        if role not in _ROLES:
            return {"ok": False, "error": f"角色必须是 {'/'.join(_ROLES)} 之一"}
        err = self._validate_credentials(username, password)
        if err:
            return {"ok": False, "error": err}
        name = username.strip()
        if self.users.get_by_name(name) is not None:
            return {"ok": False, "error": f"用户名 {name} 已存在"}
        try:
            uid = self.users.insert(
                name, self.hash_password(password), role,
                must_change_password=1 if must_change_password else 0)
        except sqlite3.IntegrityError:
            return {"ok": False, "error": f"用户名 {name} 已存在"}
        self.audit.insert(actor_user_id, "user_create", _detail_json({
            "target_user_id": uid, "username": name, "role": role}))
        return {"ok": True, "data": {"user_id": uid}}

    def change_password(self, user_id: str, old_password: str,
                        new_password: str) -> dict:
        """本人改密：验旧密码 → 更新哈希并清除初始密码标记 → 审计。"""
        row = self.users.get_by_id(user_id) if user_id else None
        if row is None or row["disabled"]:
            return {"ok": False, "error": "用户不存在或已停用"}
        stored_hash = row["pwd_hash"].encode("utf-8") if isinstance(row["pwd_hash"], str) else row["pwd_hash"]
        try:
            if not bcrypt.checkpw((old_password or "").encode("utf-8"), stored_hash):
                return {"ok": False, "error": "原密码不正确"}
        except (ValueError, TypeError):
            return {"ok": False, "error": "原密码不正确"}
        err = self._validate_credentials(row["username"], new_password)
        if err:
            return {"ok": False, "error": err}
        self.users.update_password(user_id, self.hash_password(new_password))
        self.audit.insert(user_id, "user_change_password",
                          _detail_json({"username": row["username"]}))
        return {"ok": True}

    def admin_reset_password(self, actor_user_id: str | None,
                             target_user_id: str, new_password: str) -> dict:
        """管理员重置密码：无需原密码，重置后强制对方下次登录改密。"""
        try:
            self._require_actor(actor_user_id, "manage_users")
        except PermissionError as exc:
            return {"ok": False, "error": str(exc)}
        row = self.users.get_by_id(target_user_id) if target_user_id else None
        if row is None:
            return {"ok": False, "error": "目标用户不存在"}
        err = self._validate_credentials(row["username"], new_password)
        if err:
            return {"ok": False, "error": err}
        self.users.update_password(target_user_id, self.hash_password(new_password))
        self.users.set_must_change_password(target_user_id, 1)
        self.audit.insert(actor_user_id, "user_reset_password", _detail_json({
            "target_user_id": target_user_id, "username": row["username"]}))
        return {"ok": True}

    def set_user_disabled(self, actor_user_id: str | None,
                          target_user_id: str, disabled: bool) -> dict:
        """停用/启用账号。守卫：不能停用自己；不能停用最后一名可用管理员。"""
        try:
            self._require_actor(actor_user_id, "manage_users")
        except PermissionError as exc:
            return {"ok": False, "error": str(exc)}
        row = self.users.get_by_id(target_user_id) if target_user_id else None
        if row is None:
            return {"ok": False, "error": "目标用户不存在"}
        if disabled:
            if target_user_id == actor_user_id:
                return {"ok": False, "error": "不能停用当前登录账号自己"}
            if row["role"] == "admin" and not row["disabled"]:
                others = [u for u in self.users.list_by_role("admin")
                          if u["id"] != target_user_id and not u["disabled"]]
                if not others:
                    return {"ok": False, "error": "系统至少需保留一名可用管理员"}
        self.users.set_disabled(target_user_id, disabled)
        self.audit.insert(actor_user_id,
                          "user_disable" if disabled else "user_enable",
                          _detail_json({"target_user_id": target_user_id,
                                        "username": row["username"]}))
        return {"ok": True}

    def _require_actor(self, actor_user_id: str | None, action: str) -> None:
        """账号治理的统一权限门（服务层，不依赖 UI RBAC）。"""
        if not actor_user_id or not self.check_permission(actor_user_id, action):
            raise PermissionError(f"用户 {actor_user_id or '（空）'} 无权限执行 {action}")
