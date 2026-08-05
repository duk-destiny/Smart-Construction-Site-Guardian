"""PDF 规范文档解析器（R4 双策略：条款号正则切分 + 固定窗口兜底）。
    
职责：读取动火作业规范 PDF，按条款号切分返回结构化列表。
"""
from __future__ import annotations

import re
from pathlib import Path
from core.logging import get_logger
log = get_logger(__name__)


class PdfParser:
    """规范 PDF 解析器，输出条款列表。"""

    # 条款号模式：第X条 / 第X.X.X条 / X.X.X / 一、 / (一)
    CLAUSE_PATTERN = re.compile(
        r"^\s*(?:第\s*)?([\d一二三四五六七八九十百]+(?:[\.\-\、]\d+)*)\s*(?:条|、|\.)\s*"
    )

    # 固定窗口：当条款切分不足时，按此字符数分块兜底
    FALLBACK_WINDOW = 512

    @staticmethod
    def parse(path: str | Path) -> list[dict[str, str]]:
        """解析PDF，返回 [{"clause_no": str, "clause_text": str}, ...]。
        
        异常不抛出，返回空列表并打印错误（满足 AgentBase 规范）。
        """
        try:
            import fitz  # PyMuPDF
        except ImportError:
            log.warning("PyMuPDF 未安装")
            return []

        try:
            doc = fitz.open(str(path))
            full_text = ""
            for page in doc:
                full_text += page.get_text() + "\n"
            doc.close()
        except Exception as e:
            log.warning(f"打开 {path} 失败: {e}")
            return []

        clauses = PdfParser._split_clauses(full_text.strip())
        if not clauses:
            # R4 双策略：条款切分失败 → 固定窗口兜底
            clauses = PdfParser._fallback_window(full_text.strip())
        return clauses

    @classmethod
    def _split_clauses(cls, text: str) -> list[dict[str, str]]:
        """正则条款切分。"""
        lines = text.split("\n")
        clauses: list[dict[str, str]] = []
        current_no: str | None = None
        current_lines: list[str] = []

        for line in lines:
            m = cls.CLAUSE_PATTERN.match(line)
            if m:
                if current_no is not None and current_lines:
                    clauses.append({
                        "clause_no": current_no,
                        "clause_text": " ".join(current_lines).strip(),
                    })
                current_no = m.group(1)
                current_lines = [cls.CLAUSE_PATTERN.sub("", line).strip()]
            else:
                if current_no is not None:
                    current_lines.append(line.strip())

        if current_no is not None and current_lines:
            clauses.append({
                "clause_no": current_no,
                "clause_text": " ".join(current_lines).strip(),
            })

        # 增强：如果切出的条款数 ≤ 1，视为失败（文档结构不标准或纯文本无条款号）
        if len(clauses) <= 1:
            return []
        return clauses

    @classmethod
    def _fallback_window(cls, text: str) -> list[dict[str, str]]:
        """固定窗口兜底：按 WINDOW 字符分块，编号为 section_N。"""
        if not text:
            return []
        chunks = []
        for i in range(0, len(text), cls.FALLBACK_WINDOW):
            chunk = text[i:i + cls.FALLBACK_WINDOW].strip()
            if chunk:
                chunks.append({
                    "clause_no": f"section_{i // cls.FALLBACK_WINDOW + 1}",
                    "clause_text": chunk,
                })
        return chunks
