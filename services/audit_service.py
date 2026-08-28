"""审计服务（M09）：仅 INSERT，返回 log_id（C4 不可删改）。

v0.8：新增只读导出 export_csv——审计在库内仍仅追加不可删改，
导出仅为归档/上报视角，不改变 C4 语义。
"""
from __future__ import annotations

import csv
import io
import json
import sqlite3

from dao.models import AuditDAO


class AuditService:
    """审计写入与只读导出服务。"""

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

    _CSV_HEADER = ["id", "created_at", "user_id", "username",
                   "action", "detail_json"]

    def export_csv(self, start: str | None = None,
                   end: str | None = None) -> tuple[str, int]:
        """导出审计流水为 CSV 文本，返回 (csv_text, 行数)。

        start/end 为 'YYYY-MM-DD'（end 含当日）；均空则全量。
        只读 SELECT，不触碰仅追加约束。
        """
        rows = self._dao.list_range(start=start, end=end)
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(self._CSV_HEADER)
        for r in rows:
            writer.writerow([r["id"], r["created_at"], r["user_id"] or "",
                             r["username"] or "", r["action"],
                             r["detail_json"] or ""])
        return buf.getvalue(), len(rows)
