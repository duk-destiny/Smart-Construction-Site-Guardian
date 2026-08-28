"""告警现场证据留存：把检测标注帧保存为 JPG，供管理端复核与外部推送展示。

文件落 data/alarms/ 目录（已在 .gitignore 忽略），数据库中仅存相对路径，
推送时由 notify.image_base_url 拼成可访问的完整 URL。
"""
from __future__ import annotations

import os
import re
from datetime import datetime

import cv2

from core.logging import get_logger
from core.paths import data_path, to_rel

log = get_logger(__name__)

# 目录锚定 BASE_DIR（任意 cwd 启动不破）；DB/URL 仍存相对 posix 路径
EVIDENCE_DIR = data_path("alarms")

# 文件名安全字符白名单：字(Unicode)/数字/下划线/点/横线，其余一律压成下划线。
# \w 在 str 模式下含中文，正常中文文件名不受影响。
_UNSAFE_NAME = re.compile(r"[^\w\-.]+", re.UNICODE)


def sanitize_filename(name: str | None, fallback: str = "file",
                      max_len: int = 64) -> str:
    """把用户可控文件名压成安全文件名：取 basename → 剔危险字符 → 限长。

    防两类路径穿越（v0.8）：
    - ``../`` 序列逃出目标目录；
    - 绝对路径/盘符经 os.path.join 直接覆盖前缀（Linux 下 ``/etc/x`` 即整路径替换）。
    空名/纯点等退化输入返回 fallback；超长截断。
    """
    base = os.path.basename(str(name or "").replace("\\", "/")).strip()
    safe = _UNSAFE_NAME.sub("_", base).strip("._ ")
    if not safe:
        safe = fallback
    return safe[:max_len]


def save_alarm_evidence(session_id: str | None, cls: str | None,
                        annotated_bgr) -> str | None:
    """保存标注帧，返回相对路径；失败或输入为空返回 None（不阻断告警链路）。"""
    try:
        if annotated_bgr is None or annotated_bgr.size == 0:
            return None
        os.makedirs(EVIDENCE_DIR, exist_ok=True)
        safe_session = sanitize_filename(session_id or "rt", fallback="rt", max_len=24)
        safe_cls = sanitize_filename(cls or "alarm", fallback="alarm", max_len=24)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        rel = os.path.join(EVIDENCE_DIR, f"{safe_session}_{safe_cls}_{ts}.jpg")
        ok, buf = cv2.imencode(".jpg", annotated_bgr)
        if not ok:
            return None
        with open(rel, "wb") as f:
            f.write(buf.tobytes())
        return to_rel(rel)
    except Exception as exc:  # noqa: BLE001 证据留存失败不应中断告警，但留痕
        log.warning(f"告警证据截图保存失败: {exc}")
        return None


RECTIFICATION_DIR = data_path("rectifications")


def save_rectification_photo(order_id: str, filename: str, blob: bytes) -> str | None:
    """保存整改现场照片，返回相对路径；失败返回 None（不阻断提交流程）。

    文件落 data/rectifications/<order_id>/，数据库仅存相对路径。
    """
    try:
        if not blob:
            return None
        safe_order = sanitize_filename(order_id or "wo", fallback="wo", max_len=24)
        safe_name = sanitize_filename(filename or "img.jpg", fallback="img.jpg")
        dir_ = os.path.join(RECTIFICATION_DIR, safe_order)
        os.makedirs(dir_, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        rel = os.path.join(dir_, f"{ts}_{safe_name}")
        with open(rel, "wb") as f:
            f.write(blob)
        return to_rel(rel)
    except Exception as exc:  # noqa: BLE001 照片留存失败不应中断整改提交，但留痕
        log.warning(f"整改照片保存失败: {exc}")
        return None
