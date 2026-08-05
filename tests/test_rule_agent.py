"""规范 Agent 测试（TDD：作业票校验 + 违规检索 + 培训要点）。"""

import pytest
from fpdf import FPDF
from agents.base import AgentMessage
from agents.rule_agent import RuleAgent
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
def ready_rag(tmp_path):
    """预置 RAG 的 RuleAgent fixture。"""
    p = str(tmp_path / "spec.pdf")
    _make_cjk_pdf(p, [
        "第一条 动火作业必须设置专职监火人，监火人不得擅离职守。",
        "第二条 动火现场应配备灭火器材，包括但不限于灭火器、防火毯。",
        "第三条 动火作业结束后应清除遗留火种，确认无复燃可能后方可离开。",
    ])

    chroma_dir = str(tmp_path / "chroma_rule")
    eng = RagEngine(chroma_dir=chroma_dir)
    eng.build([p])
    return RuleAgent(rag=eng)


def test_rule_agent_output(ready_rag):
    """规范 Agent 输出含 compliance + training_tips。"""
    msg = AgentMessage(
        task_id="t1", agent="rule", status="pending",
        payload={
            "permit_info": {"watcher": "", "extinguisher": "无", "fire_blanket": "未设置", "approval": "否"},
            "violation_descs": ["火花", "未戴面罩"],
        },
        error=None, cost_ms=0,
    )
    out = ready_rag.run(msg)
    assert out.status == "success", f"失败: {out.error}"
    assert "compliance" in out.payload
    assert "training_tips" in out.payload

    compliance = out.payload["compliance"]
    watcher_item = next((c for c in compliance if c["field"] == "watcher"), None)
    assert watcher_item is not None
    assert watcher_item["verdict"] == "不合规"

    assert len(out.payload["training_tips"]) >= 1


def test_rule_agent_compliance_all_pass(ready_rag):
    """全合规场景：作业票字段齐全。"""
    msg = AgentMessage(
        task_id="t2", agent="rule", status="pending",
        payload={
            "permit_info": {
                "watcher": "张三", "extinguisher": "已配备",
                "fire_blanket": "已设置", "approval": "已审批",
            },
            "violation_descs": [],
        },
        error=None, cost_ms=0,
    )
    out = ready_rag.run(msg)
    assert out.status == "success"
    verdicts = [
        c["verdict"] for c in out.payload["compliance"]
        if c["field"] in ("watcher", "extinguisher", "fire_blanket", "approval")
    ]
    assert all(v == "合规" for v in verdicts), f"verdicts: {verdicts}"


def test_rule_agent_cost_ms(ready_rag):
    """Agent 运行时记录 cost_ms。"""
    msg = AgentMessage(
        task_id="t3", agent="rule", status="pending",
        payload={"permit_info": {}, "violation_descs": []},
        error=None, cost_ms=0,
    )
    out = ready_rag.run(msg)
    assert out.cost_ms > 0, "cost_ms 应为正数"
    assert out.cost_ms < 5000, f"耗时过长: {out.cost_ms}ms (C3 RAG<1s)"
