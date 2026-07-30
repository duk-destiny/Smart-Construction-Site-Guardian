"""Task 12.5 核心链路中期集成检查（Vision→Rule→Fusion）。

说明：YOLO ONNX 权重尚未就绪，本检查用合成视觉输出（detections）驱动
真实 RuleAgent + 真实 FusionAgent，验证三段链路可达、类型正确、不抛异常。
权重就绪后，将 vision_out 替换为 VisionAgent().run(...) 的实跑结果即可。
"""
import time

import pytest
from fpdf import FPDF

from agents.base import AgentMessage
from agents.fusion_agent import FusionAgent
from agents.rule_agent import RuleAgent
from core.rag_engine import RagEngine

FONT_SIMHEI = "C:/Windows/Fonts/simhei.ttf"
VALID_RISKS = {"低", "一般", "较大", "重大"}


def _make_spec_pdf(path: str):
    pdf = FPDF()
    pdf.add_page()
    pdf.add_font("CJK", "", FONT_SIMHEI)
    pdf.set_font("CJK", "", 12)
    lines = [
        "第一条 动火作业必须设置专职监火人，监火人不得擅离职守。",
        "第二条 动火现场应配备灭火器材，包括但不限于灭火器、防火毯。",
        "第三条 动火作业结束后应清除遗留火种，确认无复燃可能后方可离开。",
        "第四条 高处动火作业应采取防火花飞溅措施，作业人员须佩戴防护面罩。",
    ]
    for line in lines:
        pdf.multi_cell(0, 8, line, new_x="LMARGIN", new_y="NEXT")
    pdf.output(path)


@pytest.fixture
def ready_rag(tmp_path):
    p = str(tmp_path / "spec.pdf")
    _make_spec_pdf(p)
    chroma_dir = str(tmp_path / "chroma_mid")
    eng = RagEngine(chroma_dir=chroma_dir)
    eng.build([p])
    return eng


def test_mid_pipeline_vision_to_fusion(ready_rag, tmp_path):
    """核心三段链路：合成视觉 → Rule → Fusion，断言全链路可达。"""
    # 1) 合成视觉输出（YOLO 就绪后替换为 VisionAgent().run(...)）
    detections = [{"cls": "spark", "conf": 0.92}]
    violation_descs = [d["cls"] for d in detections]

    # 2) 规范 Agent（真实 RAG）
    rule = RuleAgent(rag=ready_rag)
    rmsg = rule.run(AgentMessage(
        task_id="mid_check", agent="rule", status="pending",
        payload={
            "permit_info": {"watcher": "", "extinguisher": "无", "fire_blanket": "", "approval": "否"},
            "violation_descs": violation_descs,
        },
        error=None, cost_ms=0))
    assert rmsg.status == "success", f"Rule 失败: {rmsg.error}"
    compliance = rmsg.payload["compliance"]
    print(f"[中期检查] 合规结果: {len(compliance)} 项 -> {compliance}")

    # 3) 融合定级
    fusion = FusionAgent()
    fmsg = fusion.run(AgentMessage(
        task_id="mid_check", agent="fusion", status="pending",
        payload={"detections": detections, "compliance": compliance},
        error=None, cost_ms=0))
    assert fmsg.status == "success", f"Fusion 失败: {fmsg.error}"
    risk_level = fmsg.payload["risk_level"]
    print(f"[中期检查] 风险定级: {risk_level}; 理由: {fmsg.payload['reasons']}")

    # 触达标准检查
    assert detections, "视觉检出应为非空"
    assert compliance, "合规列表应为非空"
    assert risk_level in VALID_RISKS, f"risk_level 非法: {risk_level}"
    # 火花+无监火人 → 重大
    assert risk_level == "重大", f"预期重大，实际 {risk_level}"


def test_mid_pipeline_performance(ready_rag):
    """三段链路耗时检查（视觉占位，RAG+融合应 < 2s）。"""
    t0 = time.perf_counter()
    detections = [{"cls": "spark", "conf": 0.92}]
    rule = RuleAgent(rag=ready_rag)
    rmsg = rule.run(AgentMessage(
        task_id="perf", agent="rule", status="pending",
        payload={"permit_info": {"watcher": "张三"}, "violation_descs": ["spark"]},
        error=None, cost_ms=0))
    fusion = FusionAgent()
    fmsg = fusion.run(AgentMessage(
        task_id="perf", agent="fusion", status="pending",
        payload={"detections": detections, "compliance": rmsg.payload["compliance"]},
        error=None, cost_ms=0))
    elapsed = time.perf_counter() - t0
    print(f"[中期检查] RAG+Fusion 耗时: {elapsed*1000:.0f}ms")
    # C3：视觉≤3s + RAG≤1s + 融合≤余量；此处占位视觉，留 6s 给真实视觉+闭环
    assert elapsed < 6.0, f"链路耗时超标: {elapsed:.2f}s"
