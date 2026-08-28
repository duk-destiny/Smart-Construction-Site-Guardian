"""运行期媒体文件服务（Phase 3 前端需要）：DB 存相对路径 → HTTP 安全下发给图。

安全边界：入库路径相对**项目根**（data/uploads/x.jpg），解析结果必须落在
data/ 目录内（阻断 ../ 与绝对路径逃逸），且仅允许图片/视频/PDF 扩展名——
媒体端点是唯一把磁盘文件直出给浏览器的口子。
"""
from __future__ import annotations

import os

from core.paths import BASE_DIR, DATA_DIR

_ALLOWED_EXTS = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
    ".mp4": "video/mp4", ".mov": "video/quicktime",
    ".pdf": "application/pdf",
}


def resolve_media(rel: str) -> tuple[str, str]:
    """校验并解析媒体相对路径 → (绝对路径, 媒体类型)；非法/缺失抛 ValueError。

    rel 为入库的相对项目根 posix 路径（如 data/uploads/x.jpg、
    data/rectifications/w_xxx/p.png）。逐级校验：非空、扩展名白名单、
    解析后必须位于 data/ 目录内。
    """
    clean = (rel or "").strip().replace("\\", "/")
    if not clean or clean.startswith("/"):
        raise ValueError("非法的媒体路径")
    ext = os.path.splitext(clean)[1].lower()
    if ext not in _ALLOWED_EXTS:
        raise ValueError(f"不支持的媒体类型 {ext or '（无扩展名）'}")
    path = os.path.abspath(os.path.join(str(BASE_DIR), clean))
    data_root = os.path.abspath(str(DATA_DIR))
    if os.path.dirname(path) != data_root \
            and not path.startswith(data_root + os.sep):
        raise ValueError("非法的媒体路径")
    if not os.path.isfile(path):
        raise FileNotFoundError(clean)
    return path, _ALLOWED_EXTS[ext]
