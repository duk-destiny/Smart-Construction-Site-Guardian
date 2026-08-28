"""登录/改密门面（Phase 0）：UI 不再自行开连接构造 AuthService。

Phase 2 增补：user_brief 供 API 每请求复核账号态（停用/删除即时失效），
ensure_ready 承担 API 进程启动自举——api/ 只 import services，不碰 core/bootstrap。
"""
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


def user_brief(user_id: str | None) -> dict | None:
    """用户简要信息（API 鉴权复核用）；不存在返回 None。

    故意不回 pwd_hash——该函数的返回值会直接进 API 响应。
    """
    from dao.models import UserDAO
    if not user_id:
        return None
    with scoped() as conn:
        row = UserDAO(conn).get_by_id(user_id)
    if row is None:
        return None
    return {
        "user_id": row["id"],
        "username": row["username"],
        "role": row["role"],
        "disabled": bool(row["disabled"]),
        "must_change_password": bool(row["must_change_password"]),
    }


def ensure_ready() -> None:
    """进程启动自举（API 入口用）：建库 + 种子账号 + 模型注册表扫描。

    与 app.py 首屏的 ensure_initialized/ensure_models 同一套；幂等可重复调用。
    """
    from core.bootstrap import ensure_initialized, ensure_models
    ensure_initialized()
    ensure_models()
