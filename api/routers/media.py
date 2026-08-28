"""媒体文件路由（Phase 3 前端展示告警证据/整改照片/上传影像）。

路径解析与安全校验全部在 services.media_service（防穿越 + 扩展名白名单）。
认证放宽为 Bearer 头或 ?token= 查询参数二选一（<img> 无法带 header，
见 api.deps.media_auth 注释）；任何已登录角色可读。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from api.deps import media_auth
from services.media_service import resolve_media

router = APIRouter(prefix="/media", tags=["media"])


@router.get("/{rel_path:path}")
def get_media(rel_path: str, token: str = "",
              _user=Depends(media_auth)) -> FileResponse:
    """按入库相对路径（data/...）取媒体文件；非法/越界/缺失分别 400/404。"""
    try:
        path, media_type = resolve_media(rel_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="媒体文件不存在")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return FileResponse(path, media_type=media_type)
