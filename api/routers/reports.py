"""周报与导出文件路由：周报生成/预览/下载、台账 Excel 下载。

下载统一走 /api/reports/exports/{name}：文件名经 services.export_service.
load_export_file 防穿越校验（basename 一致 + 必须落在 data/exports 内）。
权限 admin+safety（周报生成/导出动作在服务层 require "export" 再强制）。
"""
from __future__ import annotations

import os
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from api.deps import require_roles
from api.schemas import WeeklyReportIn
from services import admin_console, export_service, lookup_service

router = APIRouter(tags=["reports"])

_staff = require_roles("admin", "safety")

_MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


@router.post("/reports/weekly")
def generate_weekly(body: WeeklyReportIn,
                    user=Depends(_staff)) -> dict:
    """生成风险分级周报 PDF（纯规则聚合，无 LLM），返回统计与下载地址。"""
    res = admin_console.weekly_report(body.start, body.end, user.user_id)
    if not res.get("ok"):
        raise HTTPException(status_code=500,
                            detail=res.get("error", "周报生成失败"))
    data = res.get("data") or {}
    name = os.path.basename(data.get("file_path") or "")
    return {"ok": True, "stats": data.get("stats"),
            "file": {"name": name,
                     "download_url": f"/api/reports/exports/{quote(name)}"}}


@router.get("/reports/weekly/preview")
def weekly_preview(start: str | None = None, end: str | None = None,
                   user=Depends(_staff)) -> dict:
    """周报口径只读统计（不生成文件、不写审计；留空统计全量）。"""
    return lookup_service.weekly_stats(start or None, end or None)


@router.get("/reports/exports/{name}")
def download_export(name: str, user=Depends(_staff)):
    """下载 data/exports 下的生成文件（防穿越校验在服务层）。"""
    try:
        path, safe_name = export_service.load_export_file(name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="导出文件不存在")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    media = _MEDIA_TYPES.get(os.path.splitext(safe_name)[1].lower(),
                             "application/octet-stream")
    return FileResponse(path, filename=safe_name, media_type=media)
