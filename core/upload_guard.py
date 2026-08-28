"""上传文件安全门（Phase 1）：魔数嗅探 + 大小上限（可配 config.upload.*）。

仅凭扩展名白名单不够：客户端可把任意可执行内容命名为 .jpg。
本模块按文件头魔数判定真实类型，配合各入口的大小上限共同收口。
"""
from __future__ import annotations

# 各类型的魔数判定器：data(前若干字节) -> bool
_MAGIC: dict[str, list] = {
    "jpg": [lambda b: b[:3] == b"\xff\xd8\xff"],
    "jpeg": [lambda b: b[:3] == b"\xff\xd8\xff"],
    "png": [lambda b: b[:8] == b"\x89PNG\r\n\x1a\n"],
    "pdf": [lambda b: b[:5] == b"%PDF-"],
    "mp4": [lambda b: len(b) >= 12 and b[4:8] == b"ftyp"],
    "mov": [lambda b: len(b) >= 12 and b[4:8] == b"ftyp"],
}

# 配置里 upload.max_<kind>_mb 的 kind 名 → 魔数组（别名归一）
_KIND_ALIASES = {"jpeg": "jpg", "jpg": "jpg", "png": "png",
                 "pdf": "pdf", "mp4": "mp4", "mov": "mp4"}


def sniff_kind(data: bytes) -> str | None:
    """按魔数判定真实类型，返回归一化 kind（jpg/png/pdf/mp4）或 None。"""
    for kind, checks in _MAGIC.items():
        if any(check(data) for check in checks):
            return kind
    return None


def check_upload(data: bytes, filename: str,
                 limits: dict | None = None) -> tuple[bool, str]:
    """综合校验：扩展名 → 魔数 → 大小。返回 (ok, 错误消息)。

    limits: {max_image_mb, max_video_mb, max_pdf_mb}（缺省 20/200/20，
    对齐重构方案 Phase 1 数值；由调用方从 config.upload 读入）。
    图片类（jpg/png）共用 max_image_mb；视频类（mp4/mov）共用 max_video_mb。
    """
    limits = limits or {}
    ext = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
    kind = _KIND_ALIASES.get(ext)
    if kind is None:
        return False, f"不支持的扩展名 .{ext or '（无）'}"

    if not data:
        return False, "文件内容为空"
    sniffed = sniff_kind(data)
    if sniffed is None:
        return False, "文件内容与扩展名不符（魔数校验失败）"
    if sniffed != kind:
        return False, f"扩展名 .{ext} 与实际内容 {sniffed} 不一致"

    limit_mb = {"jpg": limits.get("max_image_mb", 20),
                "png": limits.get("max_image_mb", 20),
                "pdf": limits.get("max_pdf_mb", 20),
                "mp4": limits.get("max_video_mb", 200)}[kind]
    if len(data) > int(limit_mb) * 1024 * 1024:
        return False, f"文件超过 {limit_mb}MB 上限"
    return True, ""
