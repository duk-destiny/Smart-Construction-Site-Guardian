"""PDF 规范文档解析器（R4 双策略：条款号正则切分 + 段落兜底）。

职责：读取规范 PDF，按条款切分返回结构化列表。

针对"网页另存为 PDF"类文档的清洗：
- 剥离页眉页脚/URL/页码/导航菜单等文档无关噪音（_is_chrome）；
- 按"（X）关键词"识别章节、按"第X条 / N."识别条款，clause_no 带章节前缀去撞号；
- 噪音行作为硬边界截断当前条款正文，避免"最后一条吞掉整页页眉"。
"""
from __future__ import annotations

import re
from pathlib import Path
from core.logging import get_logger

log = get_logger(__name__)


class PdfParser:
    """规范 PDF 解析器，输出条款列表。"""

    # legacy 条款号：第X条 / 第X.X条（如"第一条 ..."）
    LEGACY_CLAUSE = re.compile(r"^\s*第\s*([\d一二三四五六七八九十百]+(?:[\.\-\、]\d+)*)\s*条\b")

    # 编号条款项：1. / 1、 / 1） （网页版规范常见的"1. 必须..."）
    ITEM_CLAUSE = re.compile(r"^\s*(\d+)\s*[\.、)]\s*")

    # 章节标题：（一）"六必须" / （一）总则 （全角括号）
    SECTION = re.compile(r"^\s*\uff08[^\uff09]+\uff09")

    # 章节关键词（引号内，优先取引号内容作章节名）
    SECTION_KW = re.compile(r"[\u201c\u201d]([^\u201c\u201d]+)[\u201c\u201d]")

    # 标点集合（判断短中文行是否为导航菜单）
    _PUNCT = re.compile(r"[\d，。、；：！？（）【】《》「」]")

    @staticmethod
    def parse(path: str | Path) -> list[dict[str, str]]:
        """解析 PDF，返回 [{"clause_no": str, "clause_text": str}, ...]。

        异常不抛出，返回空列表并打印错误（满足 StageBase 规范）。
        """
        try:
            import fitz  # PyMuPDF
        except ImportError:
            log.warning("PyMuPDF 未安装")
            return []

        try:
            doc = fitz.open(str(path))
            try:
                raw_lines: list[str] = []
                for page in doc:
                    raw_lines.extend(page.get_text().split("\n"))
            finally:
                doc.close()
        except Exception as e:
            log.warning(f"打开 {path} 失败: {e}")
            return []

        clauses = PdfParser._split_clauses(raw_lines)
        if not clauses:
            # R4 双策略：条款切分不足 → 段落兜底
            clauses = PdfParser._fallback_paragraphs(raw_lines)
        return clauses

    @staticmethod
    def _is_chrome(line: str) -> bool:
        """判断是否为文档无关噪音行（页眉页脚/URL/页码/导航菜单）。"""
        s = line.replace("\xa0", "").strip()
        if not s:
            return True
        if "http://" in s or "https://" in s:
            return True
        # 页码 x/y 或纯数字页码
        if re.match(r"^\d+\s*/\s*\d+$", s) or re.match(r"^\d+$", s):
            return True
        # 日期行 2026/7/29 13:39
        if re.match(r"^\d{4}/\d{1,2}/\d{1,2}", s):
            return True
        has_cjk = bool(re.search(r"[\u4e00-\u9fff]", s))
        # 短行无中文（latin 噪音：电话分机、邮箱片段等）
        if not has_cjk and len(s) < 24:
            return True
        # 短中文行且无标点/数字（导航菜单：学院概况/本科教育/学校首）
        if has_cjk and len(s) < 6 and not PdfParser._PUNCT.search(s):
            return True
        return False

    @classmethod
    def _split_clauses(cls, lines: list[str]) -> list[dict[str, str]]:
        """正则条款切分：识别章节 → 识别条款项 → 累积正文到下一边界。

        章节标题行与噪音行不并入条款正文；遇到噪音行即截断当前条款，
        避免页眉页脚被堆进最后一条条款。
        """
        clauses: list[dict[str, str]] = []
        current_section = ""
        current_no: str | None = None
        current_lines: list[str] = []

        def flush() -> None:
            nonlocal current_no, current_lines
            if current_no is not None and current_lines:
                text = " ".join(x.strip() for x in current_lines if x.strip()).strip()
                if text:
                    clauses.append({"clause_no": current_no, "clause_text": text})
            current_no = None
            current_lines = []

        for line in lines:
            # 噪音行：硬边界，截断当前条款正文（防止整页页眉堆进最后一条）
            if cls._is_chrome(line):
                flush()
                continue
            s = line.replace("\xa0", "").strip()

            # 章节标题：（一）"六必须"
            if cls.SECTION.match(s):
                flush()
                m = cls.SECTION_KW.search(s)
                if m:
                    current_section = m.group(1).strip()
                else:
                    rest = re.sub(r"^\s*\uff08[^\uff09]+\uff09\s*", "", s).strip()
                    mm = re.match(r"\s*\uff08([^\uff09]+)\uff09", s)
                    current_section = rest or (mm.group(1) if mm else s)
                continue

            # legacy：第X条（不带章节前缀，兼容传统规范文档）
            m = cls.LEGACY_CLAUSE.match(s)
            if m:
                flush()
                current_no = m.group(1)
                current_lines = [cls.LEGACY_CLAUSE.sub("", s).strip()]
                continue

            # 编号条款项：1. / 1、（带章节前缀，去撞号）
            m = cls.ITEM_CLAUSE.match(s)
            if m:
                flush()
                num = m.group(1)
                current_no = f"{current_section}-{num}" if current_section else num
                current_lines = [cls.ITEM_CLAUSE.sub("", s).strip()]
                continue

            # 普通正文行：仅当已进入某条款时累积（序言/标题不堆进条款）
            if current_no is not None:
                current_lines.append(s)

        flush()

        # 结构不标准或纯文本无条款号 → 视为切分失败，交由段落兜底
        if len(clauses) <= 1:
            return []
        return clauses

    @classmethod
    def _fallback_paragraphs(cls, lines: list[str]) -> list[dict[str, str]]:
        """段落兜底：按噪音行分段，丢弃噪音段，保留有中文实质段。"""
        paragraphs: list[str] = []
        buf: list[str] = []
        for line in lines:
            if cls._is_chrome(line):
                if buf:
                    paragraphs.append(" ".join(buf).strip())
                    buf = []
                continue
            s = line.replace("\xa0", "").strip()
            if s:
                buf.append(s)
        if buf:
            paragraphs.append(" ".join(buf).strip())

        out: list[dict[str, str]] = []
        for i, p in enumerate(paragraphs):
            has_cjk = bool(re.search(r"[\u4e00-\u9fff]", p))
            if len(p) >= 20 and has_cjk and "http" not in p:
                out.append({"clause_no": f"section_{i + 1}", "clause_text": p})
        return out