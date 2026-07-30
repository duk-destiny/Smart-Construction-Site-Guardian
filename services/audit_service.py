"""审计服务（M09）：仅 INSERT，返回 log_id（C4 不可删改）。"""
from __future__ import annotations

import json
import sqlite3

from dao.models import AuditDAO


class AuditService:
    """审计写入服务。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self._dao = AuditDAO(conn)

    def append(self, user_id, action: str, detail: dict) -> dict:
        """写入一条审计记录，返回 {ok, data:{log_id}}。

        detail 为 dict，自动序列化为 JSON；不含明文密码/人脸/轨迹。
        """
        detail_json = json.dumps(detail, ensure_ascii=False)
        log_id = self._dao.insert(user_id, action, detail_json)
        return {"ok": True, "data": {"log_id": log_id}}
