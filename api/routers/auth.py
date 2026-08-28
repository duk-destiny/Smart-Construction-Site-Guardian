"""认证路由：登录 / 本人改密 / 我的信息。

业务全部在 services.session_entry 与 api.deps；router 只做入参与响应整形。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.deps import CurrentUser, create_access_token, get_current_user
from api.schemas import ChangePasswordIn, LoginIn
from services import session_entry

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login")
def login(body: LoginIn) -> dict:
    """登录：成功签发 JWT；失败统一 401（Phase 1 已消除用户名枚举口径）。"""
    res = session_entry.authenticate(body.username, body.password)
    if not res.get("ok"):
        raise HTTPException(status_code=401,
                            detail=res.get("error", "用户名或密码错误"))
    token, expires_in = create_access_token(
        res["user_id"], body.username, res["role"],
        must_change_password=res.get("must_change_password", False))
    return {
        "token": token,
        "token_type": "bearer",
        "expires_in": expires_in,
        "user_id": res["user_id"],
        "username": body.username,
        "role": res["role"],
        "must_change_password": bool(res.get("must_change_password")),
    }


@router.post("/change-password")
def change_password(body: ChangePasswordIn,
                    user: CurrentUser = Depends(get_current_user)) -> dict:
    """本人改密（首登强制改密流程共用；成功即清除初始密码标记）。"""
    res = session_entry.change_own_password(user.user_id,
                                            body.old_password,
                                            body.new_password)
    if not res.get("ok"):
        raise HTTPException(status_code=400,
                            detail=res.get("error", "修改失败"))
    return {"ok": True}


@router.get("/me")
def me(user: CurrentUser = Depends(get_current_user)) -> dict:
    """当前登录用户信息（角色/改密标记取 DB 实时值）。"""
    return {"user_id": user.user_id, "username": user.username,
            "role": user.role,
            "must_change_password": user.must_change_password}
