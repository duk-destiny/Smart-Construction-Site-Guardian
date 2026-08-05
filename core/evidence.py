"""告警现场证据留存：把检测标注帧保存为 JPG，供管理端复核与外部推送展示。

文件落 data/alarms/ 目录（已在 .gitignore 忽略），数据库中仅存相对路径，
推送时由 notify.image_base_url 拼成可访问的完整 URL。
"""
from __future__ import annotations

import os
import re
from datetime import datetime

import cv2

EVIDENCE_DIR = os.path.join("data", "alarms")


def save_alarm_evidence(session_id: str | None, cls: str | None,
                        annotated_bgr) -> str | None:
    """保存标注帧，返回相对路径；失败或输入为空返回 None（不阻断告警链路）。"""
    try:
        if annotated_bgr is None or annotated_bgr.size == 0:
            return None
        os.makedirs(EVIDENCE_DIR, exist_ok=True)
        safe_session = re.sub(r"[^\w\-.]+", "_", str(session_id or "rt"))[:24]
        safe_cls = re.sub(r"[^\w\-.]+", "_", str(cls or "alarm"))[:24]
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        rel = os.path.join(EVIDENCE_DIR, f"{safe_session}_{safe_cls}_{ts}.jpg")
        ok, buf = cv2.imencode(".jpg", annotated_bgr)
        if not ok:
            return None
        with open(rel, "wb") as f:
            f.write(buf.tobytes())
        return rel
    except Exception:  # noqa: BLE001 证据留存失败不应中断告警
        return None
