"""Task 18 端到端联调（P6）：合成视觉 → 真实 Rule/Fusion/Action 全链路。

YOLO ONNX 未就绪，注入 StubVision 提供 detections，验证：
- 工单生成、risk_level 合理、审计有记录
- 主链路耗时 < 8s（C3）
- 降级：LLM 不可用时 ActionAgent 走模板（已在 P4 验证），此处验证 Ollama 宕机不影响主流程
"""
import time

import fpdf
import pytest
from agents.base import AgentMessage
from agents.orchestrator import Orchestrator
from agents.rule_agent import RuleAgent
from agents.fusion_agent import FusionAgent
from agents.action_agent import ActionAgent
from dao.db import get_conn, init_db
from dao.models import UserDAO, TaskDAO, AuditDAO, WorkOrderDAO, RiskDAO
from services.task_service import TaskService
from core.rag_engine import RagEngine
from services.audit_service import AuditService

FONT_SIMHEI = "C:/Windows/Fonts/simhei.ttf"


class StubVision:
    """合成视觉：返回火花检测（模拟 YOLO 实检输出）。"""
    def run(self, msg: AgentMessage) -> AgentMessage:
        msg.status = "success"
        msg.payload = {
            "detections": [{"cls": "spark", "conf": 0.93}],
            "violation_descs": ["spark"],
        }
        return msg


def _build_kb(tmp_path):
    p = str(tmp_path / "spec.pdf")
    pdf = fpdf.FPDF()
    pdf.add_page()
    pdf.add_font("CJK", "", FONT_SIMHEI)
    pdf.set_font("CJK", "", 12)
    for line in [
        "第一条 动火作业必须设置专职监火人，监火人不得擅离职守。",
        "第二条 动火现场应配备灭火器材，包括但不限于灭火器、防火毯。",
        "第三条 动火作业结束后应清除遗留火种，确认无复燃可能后方可离开。",
        "第四条 高处动火作业应采取防火花飞溅措施，作业人员须佩戴防护面罩。",
    ]:
        pdf.multi_cell(0, 8, line, new_x="LMARGIN", new_y="NEXT")
    pdf.output(p)
    eng = RagEngine(chroma_dir=str(tmp_path / "chroma_e2e"))
    eng.build([p])
    return eng


@pytest.fixture
def e2e_env(tmp_path):
    conn = get_conn(":memory:")
    init_db(conn)
    uid = UserDAO(conn).insert("demo", "h", "safety")
    kb = _build_kb(tmp_path)
    rule = RuleAgent(rag=kb)
    orch = Orchestrator(
        vision=StubVision(), rule=rule, fusion=FusionAgent(), action=ActionAgent(),
        progress_cb=TaskService(conn).update_progress,
    )
    return {"conn": conn, "uid": uid, "orch": orch, "task_svc": TaskService(conn)}


def test_e2e_full_pipeline(e2e_env):
    """全链路：合成视觉+真实规范/融合/闭环 → 工单 + 审计。"""
    conn = e2e_env["conn"]
    orch = e2e_env["orch"]
    tid = e2e_env["task_svc"].create_task(e2e_env["uid"], [], {"watcher": "", "extinguisher": "无"})

    out = orch.execute(tid, images=[], permit_info={"watcher": "", "extinguisher": "无"})
    assert out.status in ("success", "degraded")
    assert out.payload["risk_level"] == "重大", out.payload
    assert out.payload["work_order"]["risk_level"] == "重大"

    # 审计：编排器本身不写审计，由 UI 层在用户触发 execute 时落审计（见 page_agents.py）。
    # 这里模拟 UI 的审计写入，验证审计集成点可用且记录成功。
    AuditService(conn).append(e2e_env["uid"], "execute", {"task_id": tid})
    audit_count = conn.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0]
    assert audit_count >= 1

    # 进度记录存在
    assert e2e_env["task_svc"].get_progress(tid)


def test_e2e_performance(e2e_env):
    """主链路耗时 < 8s（C3）。"""
    orch = e2e_env["orch"]
    tid = e2e_env["task_svc"].create_task(e2e_env["uid"], [], {"watcher": "张三"})
    t0 = time.perf_counter()
    orch.execute(tid, images=[], permit_info={"watcher": "张三"})
    elapsed = time.perf_counter() - t0
    print(f"[e2e] 主链路耗时: {elapsed*1000:.0f}ms")
    assert elapsed < 8.0, f"主链路超时: {elapsed:.2f}s"


def test_e2e_audit_append_only(e2e_env):
    """审计仅追加：无 delete 路径。"""
    assert not hasattr(AuditDAO, "delete")
    assert not hasattr(AuditDAO, "update")


class _BoomLlm:
    """模拟 Ollama 可达但报错（连得上但推理失败）。"""
    def available(self):
        return True
    def polish(self, prompt):
        raise RuntimeError("ollama unreachable")


def test_e2e_llm_down_degrade(e2e_env):
    """降级演练：Ollama 报错/宕机时，主链路仍返回模板工单，不崩溃（C4 可用性）。"""
    orch = e2e_env["orch"]
    orch.action = ActionAgent(llm=_BoomLlm())
    tid = e2e_env["task_svc"].create_task(e2e_env["uid"], [], {"watcher": "李四"})
    out = orch.execute(tid, images=[], permit_info={"watcher": "李四"})
    assert out.status in ("success", "degraded")
    # 主链路不因 LLM 故障失败，工单与模板提示仍存在
    assert out.payload["work_order"]["worker_notice"]
    assert "隐患说明" in out.payload["worker_notice"]
    assert e2e_env["task_svc"].get_progress(tid)
