"""登录/改密门面（Phase 0）：UI 不再自行开连接构造 AuthService。"""
from __future__ import annotations

from services.auth_service import AuthService
from services.db import scoped


def authenticate(username: str, password: str) -> dict:
    """登录校验：服务自持连接，返回 AuthService.login 结果。"""
    with scoped() as conn:
        return AuthService(conn).login(username, password)


def change_own_password(user_id: str | None, old_password: str,
                        new_password: str) -> dict:
    """本人改密（顶栏入口/首登强制改密共用）。"""
    with scoped() as conn:
        return AuthService(conn).change_password(user_id, old_password,
                                                 new_password)
