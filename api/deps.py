"""API 认证依赖：JWT（HS256）签发/校验 + 角色门。

密钥取值优先级：环境变量 API_JWT_SECRET > config.api.jwt_secret
（config 值本身支持 ${ENV} 展开，v0.8 机制）；两者皆空时回退进程内随机值——
重启即全体登录态失效，仅适合本机开发；离线生产必须在配置/环境注入固定密钥。
过期默认 12h（api.token_expire_hours 可调）。

每次请求经 services.session_entry.user_brief 复核账号存在性与停用态：
账号被停用/删除后即使 token 未过期也立即失效（与 v0.8「停用后下一次
鉴权即被拒」口径一致）。角色以 DB 实时值为准，JWT 中的 role 仅作签发参考。
"""
from __future__ import annotations

import os
import secrets
import time
from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, Request

from core.config import shared_config  # 白名单（情况1）：只读配置
from core.logging import get_logger    # 白名单（情况1）：日志
from services import session_entry

log = get_logger(__name__)

# 进程级兜底密钥：未配置时生成一次（同进程内签发/校验自洽）
_FALLBACK_SECRET: str | None = None


def _jwt_secret() -> str:
    global _FALLBACK_SECRET
    env = os.environ.get("API_JWT_SECRET", "").strip()
    if env:
        return env
    conf = shared_config().get("api") or {}
    secret = str(conf.get("jwt_secret") or "").strip()
    if secret:
        return secret
    if _FALLBACK_SECRET is None:
        _FALLBACK_SECRET = secrets.token_urlsafe(48)
        log.warning("未配置 api.jwt_secret / API_JWT_SECRET，"
                    "回退进程内随机密钥（重启后所有登录态失效）")
    return _FALLBACK_SECRET


def _expire_hours() -> float:
    conf = shared_config().get("api") or {}
    try:
        hours = float(conf.get("token_expire_hours") or 12)
    except (TypeError, ValueError):
        hours = 12.0
    return max(0.5, hours)


def create_access_token(user_id: str, username: str, role: str,
                        must_change_password: bool = False) -> tuple[str, int]:
    """签发 JWT，返回 (token, 有效期秒数)。"""
    hours = _expire_hours()
    now = int(time.time())
    payload = {"sub": user_id, "name": username, "role": role,
               "mcp": bool(must_change_password),
               "iat": now, "exp": now + int(hours * 3600)}
    token = jwt.encode(payload, _jwt_secret(), algorithm="HS256")
    return token, int(hours * 3600)


def decode_token(token: str) -> dict:
    """解码并校验 JWT；失败抛 HTTPException(401)，消息区分过期/无效。"""
    try:
        return jwt.decode(token, _jwt_secret(), algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="登录状态无效，请重新登录")


def try_decode(token: str) -> dict | None:
    """WebSocket 用：不抛异常的解码，失败返回 None（由路由关闭连接）。"""
    try:
        return jwt.decode(token or "", _jwt_secret(), algorithms=["HS256"])
    except jwt.InvalidTokenError:
        return None


@dataclass
class CurrentUser:
    """请求级身份（字段均来自 DB 实时值，非 JWT 声明）。"""

    user_id: str
    username: str
    role: str
    must_change_password: bool


def _extract_bearer(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="缺少认证信息，请先登录",
                            headers={"WWW-Authenticate": "Bearer"})
    return auth[len("Bearer "):].strip()


def get_current_user(request: Request) -> CurrentUser:
    """认证依赖：JWT 校验 + DB 复核（停用/删除即时失效）。"""
    payload = decode_token(_extract_bearer(request))
    brief = session_entry.user_brief(payload.get("sub"))
    if brief is None or brief["disabled"]:
        raise HTTPException(status_code=401, detail="账号不存在或已停用")
    return CurrentUser(user_id=brief["user_id"], username=brief["username"],
                       role=brief["role"],
                       must_change_password=brief["must_change_password"])


def require_roles(*roles: str):
    """角色门工厂：角色不匹配返回 403；细粒度动作权限仍由服务层强制。"""
    allowed = set(roles)

    def _dep(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.role not in allowed:
            raise HTTPException(status_code=403, detail="当前角色无权访问该资源")
        return user

    return _dep


def media_auth(request: Request, token: str = "") -> None:
    """媒体下发的放宽认证：Bearer 头 **或** 查询参数 token 二选一。

    背景：<img> 标签无法自定义 header，前端媒体 URL 以 ?token= 携带 JWT
    （仅限内网单机部署；URL 可能进访问日志，生产外网暴露面不要开此端点）。
    复核逻辑与 get_current_user 一致（停用即失效）。
    """
    auth = request.headers.get("Authorization", "")
    payload = None
    if auth.startswith("Bearer "):
        payload = try_decode(auth[len("Bearer "):].strip())
    elif token:
        payload = try_decode(token)
    if payload is None:
        raise HTTPException(status_code=401, detail="缺少认证信息，请先登录")
    brief = session_entry.user_brief(payload.get("sub"))
    if brief is None or brief["disabled"]:
        raise HTTPException(status_code=401, detail="账号不存在或已停用")
