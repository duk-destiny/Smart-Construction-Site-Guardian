"""PDF 解析器测试（TDD：合成中文 PDF + 条款切分 + 兜底窗口）。"""

import pytest
from fpdf import FPDF
from core.pdf_parser import PdfParser

FONT_SIMHEI = "C:/Windows/Fonts/simhei.ttf"


def _make_cjk_pdf(save_path: str, lines: list[str]):
    """用 fpdf2 + 黑体生成含中文的 PDF。"""
    pdf = FPDF()
    pdf.add_page()
    pdf.add_font("CJK", "", FONT_SIMHEI)
    pdf.set_font("CJK", "", 12)
    for line in lines:
        pdf.multi_cell(0, 8, line, new_x="LMARGIN", new_y="NEXT")
    pdf.output(save_path)


@pytest.fixture
def sample_pdf(tmp_path):
    """合成一份带条款号的测试规范 PDF。"""
    p = str(tmp_path / "sample_spec.pdf")
    _make_cjk_pdf(p, [
        "第一条 为规范动火作业安全管理，制定本规范。",
        "第二条 凡在禁火区进行焊接、切割等产生明火的作业，均按本规范执行。",
        "第十条 动火作业必须设置专职监火人，监火人不得擅离职守。",
        "第十五条 动火现场必须配备灭火器、防火毯等消防器材。",
        "第二十条 动火作业结束后，监火人应监护确认无遗留火种方可离开。",
    ])
    return p


def test_parse_clauses(sample_pdf):
    """条款号正则切分：至少提取 3 条以上。"""
    clauses = PdfParser.parse(sample_pdf)
    assert len(clauses) >= 3, f"条款切分不足: {len(clauses)} 条"
    for c in clauses:
        assert "clause_no" in c
        assert "clause_text" in c
        assert len(c["clause_text"]) > 0


def test_parse_clause_text_contains_keywords(sample_pdf):
    """条款正文包含规范关键词。"""
    clauses = PdfParser.parse(sample_pdf)
    texts = " ".join(c["clause_text"] for c in clauses)
    assert "监火人" in texts, f"应包含监火人，实际: {texts[:200]}"
    assert "灭火器" in texts, f"应包含灭火器，实际: {texts[:200]}"


def test_empty_pdf(tmp_path):
    """空 PDF 返回空列表不崩溃。"""
    p = str(tmp_path / "empty.pdf")
    pdf = FPDF()
    pdf.add_page()
    pdf.output(p)
    result = PdfParser.parse(p)
    assert isinstance(result, list)


def test_unparseable_fallback_window(tmp_path):
    """无条款号纯文本 → 走固定窗口兜底。"""
    p = str(tmp_path / "plain.pdf")
    _make_cjk_pdf(p, ["动火现场应配备灭火器材。动火作业需要审批。作业现场禁止吸烟。" * 80])
    clauses = PdfParser.parse(p)
    assert len(clauses) >= 1
    assert clauses[0]["clause_no"].startswith("section_")
