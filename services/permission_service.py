"""服务层权限校验：业务 Service 不再只依赖 UI 层 RBAC。"""
from __future__ import annotations

import sqlite3

from services.auth_service import AuthService, AuthorizationError


class PermissionService:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._auth = AuthService(conn)

    def require(self, user_id: str | None, action: str) -> None:
        """校验 user_id 是否允许 action，不通过则抛 AuthorizationError。"""
        if not user_id:
            raise AuthorizationError("缺少用户身份")
        if not self._auth.check_permission(user_id, action):
            raise AuthorizationError(f"用户 {user_id} 无权限执行 {action}")
