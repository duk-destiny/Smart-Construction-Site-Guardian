"""闭环处置 Agent（M06）：组装整改工单，生成工人白话提示，落库 work_orders。

worker_notice = LlmEngine.polish（可选，异步不计时）或模板降级拼接（LLD §3.5/§5.1）。
"""
from __future__ import annotations

import threading

from agents.base import AgentBase, AgentMessage
from core.llm_engine import LlmEngine

# 固定整改要求话术（降级路径）
_TEMPLATE_REQUIREMENT = (
    "请立即停止相关动火作业，落实以下整改：①指定专职监火人并全程监护；"
    "②配备合格灭火器材（灭火器/防火毯）；③清理周边易燃物；"
    "④补办/核验作业审批手续。整改完成并经复查合格后方可恢复作业。"
)


class ActionAgent(AgentBase):
    """闭环处置 Agent。"""

    def __init__(self, llm: LlmEngine | None = None, work_order_dao=None):
        self._llm = llm or LlmEngine()
        self._wo_dao = work_order_dao

    def _template(self, hazard_desc: str, clause_text: str) -> str:
        """模板降级：规范原文 + 固定整改话术。"""
        return (
            f"隐患说明：{hazard_desc}\n"
            f"违反规范：{clause_text}\n"
            f"整改要求：{_TEMPLATE_REQUIREMENT}"
        )

    def _execute(self, msg: AgentMessage) -> AgentMessage:
        risk_level = msg.payload.get("risk_level", "一般")
        reasons = msg.payload.get("reasons", []) or []
        compliance = msg.payload.get("compliance", []) or []
        training_tips = msg.payload.get("training_tips", []) or []

        # 隐患描述：来自融合理由 / 违规项
        hazard_parts = list(reasons)
        if not hazard_parts and compliance:
            hazard_parts = [c.get("label", "") for c in compliance if c.get("verdict") != "合规"]
        hazard_desc = "；".join([h for h in hazard_parts if h]) or "检测到动火作业安全隐患"

        # 违反规范条款：取 RAG 命中的首条 clause
        clause_text = ""
        for c in compliance:
            if c.get("verdict") in ("不合规", "待核查") and c.get("clause_ref"):
                clause_text = f"第{c['clause_ref']}条"
                break
        if not clause_text and training_tips:
            clause_text = training_tips[0][:60]

        # 主链路：立即返回模板（同步、快速，计入 ≤8s）
        worker_notice = self._template(hazard_desc, clause_text)

        # 异步润色（LLD §5.1：不计入主链路耗时）
        if self._llm.available() and msg.task_id:
            threading.Thread(
                target=self._polish_async,
                args=(msg.task_id, hazard_desc, clause_text),
                daemon=True,
            ).start()

        work_order = {
            "risk_level": risk_level,
            "hazard_desc": hazard_desc,
            "clause": clause_text,
            "requirement": _TEMPLATE_REQUIREMENT,
            "worker_notice": worker_notice,
            "training_tips": training_tips,
        }
        msg.status = "success"
        msg.payload = {"work_order": work_order, "worker_notice": worker_notice}
        return msg

    def _polish_async(self, task_id: str, hazard_desc: str, clause_text: str) -> None:
        """后台线程润色，完成后回填工单（不阻塞主流程）。"""
        if self._wo_dao is None:
            return
        try:
            polished = self._llm.polish(
                f"请用一线工人听得懂的大白话，提醒他注意火灾隐患并说明整改要求："
                f"{hazard_desc}。规范：{clause_text}")
            if polished:
                self._wo_dao.update_notice(task_id, polished)
        except Exception:
            pass
