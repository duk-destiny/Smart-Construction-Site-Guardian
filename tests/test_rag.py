"""RAG 引擎测试（TDD：构建 + 检索 + 降级）。"""

import pytest
from fpdf import FPDF
from core.rag_engine import RagEngine
from tests.cjk_font import cjk_font_path

FONT_SIMHEI = cjk_font_path()


import os
if not os.path.isdir("data/models/BAAI--bge-small-zh-v1.5/snapshots/master"):
    pytest.skip("BGE embedding model not present; RAG tests skipped", allow_module_level=True)

def _make_cjk_pdf(save_path: str, lines: list[str]):
    pdf = FPDF()
    pdf.add_page()
    pdf.add_font("CJK", "", FONT_SIMHEI)
    pdf.set_font("CJK", "", 12)
    for line in lines:
        pdf.multi_cell(0, 8, line, new_x="LMARGIN", new_y="NEXT")
    pdf.output(save_path)


@pytest.fixture
def rag_pdf(tmp_path):
    """合成规范 PDF 用于 RAG 测试。"""
    p = str(tmp_path / "spec.pdf")
    _make_cjk_pdf(p, [
        "第一条 动火作业必须设置专职监火人。",
        "第二条 动火现场应配备灭火器材。",
        "第三条 动火作业结束后应清除遗留火种。",
        "第四条 动火作业前须办理作业审批手续。",
        "第五条 高处动火作业应采取防火花飞溅措施。",
    ])
    return p


def test_build_and_query(rag_pdf, tmp_path):
    """构建知识库后 query 返回 top_k 条结果。"""
    chroma_dir = str(tmp_path / "chroma_test")
    eng = RagEngine(chroma_dir=chroma_dir)
    count = eng.build([rag_pdf])
    assert count >= 3, f"入库条款数不足: {count}"

    results = eng.query("动火作业需要安排监火人", top_k=3)
    assert 1 <= len(results) <= 3, f"查询结果数异常: {len(results)}"
    assert results[0]["score"] >= 0
    assert "监火人" in results[0]["clause_text"] or "动火" in results[0]["clause_text"]


def test_query_empty_collection():
    """未 build 先 query 不崩溃，返回空列表。"""
    eng = RagEngine(chroma_dir=":memory:")
    results = eng.query("监火人", top_k=3)
    assert isinstance(results, list)
    assert len(results) == 0


def test_build_empty_pdf_list():
    """空 PDF 列表返回 0 不崩溃。"""
    eng = RagEngine()
    count = eng.build([])
    assert count == 0
