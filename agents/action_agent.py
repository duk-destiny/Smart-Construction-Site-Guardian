"""闭环处置 Agent（M06）：组装整改工单，生成工人白话提示，落库 work_orders。

worker_notice = LlmEngine.polish（可选，异步不计时）或模板降级拼接（LLD §3.5/§5.1）。
"""
from __future__ import annotations

import threading

from agents.base import AgentBase, AgentMessage
from core.llm_engine import LlmEngine

# 按风险等级动态生成整改要求话术（降级路径）
# 避免低风险场景仍套用"立即停止作业"等过度严厉的固定模板
_REQUIREMENTS = {
    "低": (
        "当前影像/作业票未识别出明确违规项，建议保持常规安全巡检并留存本次检测记录。"
    ),
    "一般": (
        "存在一般隐患，请现场负责人按规范要求限期整改，并拍照确认整改完成情况。"
    ),
    "较大": (
        "存在较大安全隐患，请立即停止相关危险作业，落实监火人、灭火器材及易燃物清理等整改措施，"
        "整改完成并经复核后方可复工。"
    ),
    "重大": (
        "存在重大安全隐患，须立即停止相关动火作业并撤离无关人员，按规范落实整改，"
        "经安全部门复核合格后方可复工。"
    ),
}

_DEADLINES = {
    "低": "无需限期",
    "一般": "24小时内",
    "较大": "2小时内",
    "重大": "立即",
}


class ActionAgent(AgentBase):
    """闭环处置 Agent。"""

    def __init__(self, llm: LlmEngine | None = None, work_order_dao=None):
        self._llm = llm or LlmEngine()
        self._wo_dao = work_order_dao

    def _template(self, hazard_desc: str, clause_text: str, risk_level: str) -> str:
        """模板降级：规范原文 + 按风险等级的整改话术。"""
        requirement = _REQUIREMENTS.get(risk_level, _REQUIREMENTS["一般"])
        deadline = _DEADLINES.get(risk_level, "限期整改")
        # clause_text 为空时避免"违反规范："后面空白
        clause_display = clause_text or "本次未匹配到具体规范条款，请以影像和作业票信息为准。"
        return (
            f"隐患说明：{hazard_desc}\n"
            f"违反规范：{clause_display}\n"
            f"整改要求：{requirement}\n"
            f"处理时限：{deadline}"
        )

    def _execute(self, msg: AgentMessage) -> AgentMessage:
        risk_level = msg.payload.get("risk_level", "一般")
        reasons = msg.payload.get("reasons", []) or []
        compliance = msg.payload.get("compliance", []) or []
        training_tips = msg.payload.get("training_tips", []) or []

        # 隐患描述：低风险场景不要套用"检测到动火作业安全隐患"
        hazard_parts = list(reasons)
        if not hazard_parts and compliance:
            hazard_parts = [
                c.get("label", "") for c in compliance if c.get("verdict") != "合规"
            ]
        if hazard_parts:
            hazard_desc = "；".join([h for h in hazard_parts if h])
        elif risk_level == "低":
            hazard_desc = "未检出明确违规目标，现场状况良好"
        else:
            hazard_desc = "检测到动火作业安全隐患"

        # 违反规范条款：优先用 RAG 命中的条款原文（管理员上传 PDF 的真实内容），
        # 编号作锚点；只传"第X条"光编号会让 LLM 凭训练知识编造法规名（安全系统硬伤）
        clause_text = ""
        for c in compliance:
            if c.get("verdict") in ("不合规", "待核查") and (c.get("clause_ref") or c.get("clause_text")):
                ct = (c.get("clause_text") or "").strip()
                cn = (c.get("clause_ref") or c.get("clause_no") or "").strip()
                if ct and cn:
                    clause_text = f"第{cn}条：{ct}"
                elif ct:
                    clause_text = ct
                elif cn:
                    clause_text = f"第{cn}条"
                break
        if not clause_text and training_tips:
            clause_text = training_tips[0][:60]

        requirement = _REQUIREMENTS.get(risk_level, _REQUIREMENTS["一般"])
        deadline = _DEADLINES.get(risk_level, "限期整改")

        # 主链路：立即返回模板（同步、快速，计入 ≤8s）
        worker_notice = self._template(hazard_desc, clause_text, risk_level)

        # 异步润色改为工单落库后由调用方触发 polish()，
        # 避免在 work_orders 行写入前回填（update_notice 命中 0 行）
        work_order = {
            "risk_level": risk_level,
            "hazard_desc": hazard_desc,
            "clause": clause_text,
            "requirement": requirement,
            "deadline": deadline,
            "worker_notice": worker_notice,
            "training_tips": training_tips,
            "review_required": msg.payload.get("needs_review", False),
            "review_reasons": msg.payload.get("review_reasons", []),
        }
        msg.status = "success"
        msg.payload = {
            "work_order": work_order,
            "worker_notice": worker_notice,
            "input_summary": {
                "risk_level": risk_level,
                "reasons_count": len(reasons),
                "needs_review": msg.payload.get("needs_review", False),
            },
        }
        return msg

    def polish(self, task_id: str, hazard_desc: str, clause_text: str,
               requirement: str, deadline: str) -> None:
        """工单落库后调用：LLM 可用则异步润色工人提示并回填 DB（不阻塞主链路）。

        必须在 work_orders.insert 之后触发，保证 update_notice 命中已有行。
        """
        if self._wo_dao is None or not self._llm.available() or not task_id:
            return
        threading.Thread(
            target=self._polish_async,
            args=(task_id, hazard_desc, clause_text, requirement, deadline),
            daemon=True,
        ).start()

    def _polish_async(
        self,
        task_id: str,
        hazard_desc: str,
        clause_text: str,
        requirement: str,
        deadline: str,
    ) -> None:
        """后台线程润色，完成后回填工单（不阻塞主流程）。"""
        if self._wo_dao is None:
            return
        try:
            clause_display = clause_text or "本次未匹配到具体规范条款"
            polished = self._llm.polish(
                f"请用一线工人听得懂的大白话，提醒他注意动火作业安全："
                f"隐患说明：{hazard_desc}；规范依据（条款原文，勿增改、勿编造法规名称）：{clause_display}；"
                f"整改要求：{requirement}；处理时限：{deadline}。"
                f"请严格依据以上信息组织语言，不要编造未给出的内容。"
            )
            if polished:
                self._wo_dao.update_notice(task_id, polished)
        except Exception:
            pass
