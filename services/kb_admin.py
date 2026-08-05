"""知识库管理（admin）：规范 PDF 导入 → 解析 → 向量化入库。

供管理端页调用；导入即解析+切分+向量化（Chroma 持久化）。
"""
from __future__ import annotations

import sqlite3

from core.pdf_parser import PdfParser
from core.rag_engine import RagEngine
from dao.models import KbDocDAO
from services.permission_service import PermissionService


class KbAdmin:
    """知识库导入服务。"""

    def __init__(self, conn: sqlite3.Connection, chroma_dir: str = "data/kb/chroma",
                 collection: str = "kb_hot_work", bge_dir: str | None = None):
        self.conn = conn
        self.chroma_dir = chroma_dir
        self.collection = collection
        self.bge_dir = bge_dir
        self.permissions = PermissionService(conn)

    def import_pdf(self, pdf_path: str, imported_by: str) -> dict:
        """导入一份规范 PDF：解析 → 向量化 → 登记。"""
        self.permissions.require(imported_by, "import_pdf")
        clauses = PdfParser.parse(pdf_path)
        if not clauses:
            return {"ok": False, "error": "解析失败或为空"}

        eng = RagEngine(bge_dir=self.bge_dir, chroma_dir=self.chroma_dir,
                        collection_name=self.collection)
        count = eng.build([pdf_path])
        KbDocDAO(self.conn).insert(pdf_path.split("/")[-1], count, imported_by)
        return {"ok": True, "chunks": count, "clauses": len(clauses)}

    def list_docs(self):
        return KbDocDAO(self.conn).list_all()
