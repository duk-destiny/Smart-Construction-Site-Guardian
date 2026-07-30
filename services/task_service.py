"""任务服务（M02 支撑）：任务创建、进度追踪、人工改判、结果持久化。

进度字典存内存（供页面轮询）；改判落 DB（risks 表）+ 写审计（C4）。
"""
from __future__ import annotations

import json
import sqlite3

from dao.models import (
    TaskDAO, RiskDAO, DetectionDAO, ComplianceDAO, WorkOrderDAO,
)


class TaskService:
    """任务生命周期服务。"""

    # 内存进度：task_id -> {agent: {status, cost_ms}}
    _progress: dict[str, dict] = {}

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.tasks = TaskDAO(conn)
        self.risks = RiskDAO(conn)
        self.detections = DetectionDAO(conn)
        self.compliances = ComplianceDAO(conn)
        self.work_orders = WorkOrderDAO(conn)

    def create_task(self, user_id: str, files: list[str], permit_info: dict) -> str:
        """创建任务，返回 task_id；写审计由调用方负责。"""
        tid = self.tasks.insert(user_id, json.dumps(permit_info, ensure_ascii=False), "running")
        TaskService._progress[tid] = {}
        return tid

    def update_progress(self, task_id: str, agent: str, status: str, cost_ms: int = 0) -> None:
        """供 Orchestrator 回调，更新某 Agent 的进度。"""
        prog = TaskService._progress.setdefault(task_id, {})
        prog[agent] = {"status": status, "cost_ms": cost_ms}

    def get_progress(self, task_id: str) -> dict:
        """返回 {agent: {status, cost_ms}}。"""
        return dict(TaskService._progress.get(task_id, {}))

    def manual_override(self, task_id: str, new_level: str, reason: str) -> bool:
        """人工改判风险等级（写审计在调用方）。"""
        row = self.risks.get_by_task(task_id)
        if row is None:
            return False
        self.risks.override(row["id"], new_level, reason)
        return True

    def save_result(self, task_id: str, agent_results: dict) -> None:
        """持久化研判全链条结果（幂等：同一 task_id 已存在则跳过）。

        agent_results: {vision: AgentMessage, rule: AgentMessage,
                        fusion: AgentMessage, action: AgentMessage}
        """
        # 幂等判断：已有工单记录则跳过
        if self.work_orders.get_by_task(task_id) is not None:
            return

        vision_payload = (agent_results.get("vision", {}).get("payload")
                          if isinstance(agent_results.get("vision"), dict) else {}) or {}
        rule_payload = (agent_results.get("rule", {}).get("payload")
                        if isinstance(agent_results.get("rule"), dict) else {}) or {}
        fusion_payload = (agent_results.get("fusion", {}).get("payload")
                          if isinstance(agent_results.get("fusion"), dict) else {}) or {}
        action_payload = (agent_results.get("action", {}).get("payload")
                          if isinstance(agent_results.get("action"), dict) else {}) or {}

        # 1) 视觉检测结果
        det_rows: list[dict] = []
        for d in vision_payload.get("detections") or []:
            det_rows.append({
                "task_id": task_id,
                "frame_path": None,
                "cls": d.get("cls", ""),
                "conf": float(d.get("conf", 0)),
                "bbox_json": json.dumps(d.get("bbox", []), ensure_ascii=False),
                "violation_desc": d.get("violation_desc", ""),
            })
        if det_rows:
            self.detections.bulk_insert(det_rows)

        # 2) 规范合规结果
        comp_rows: list[dict] = []
        for c in rule_payload.get("compliance") or []:
            comp_rows.append({
                "task_id": task_id,
                "verdict": c.get("verdict", ""),
                "clause_no": c.get("label", ""),
                "clause_text": c.get("clause_text", ""),
                "score": None,
            })
        if comp_rows:
            self.compliances.bulk_insert(comp_rows)

        # 3) 融合风险
        risk_level = fusion_payload.get("risk_level") or action_payload.get("risk_level") or "一般"
        self.risks.insert(
            task_id=task_id,
            risk_level=risk_level,
            reasons_json=json.dumps(fusion_payload.get("reasons") or [], ensure_ascii=False),
            filtered_fp_json=json.dumps(fusion_payload.get("filtered_fp") or [], ensure_ascii=False),
        )

        # 4) 工单
        wo = action_payload.get("work_order") or {}
        self.work_orders.insert(
            task_id=task_id,
            hazard_desc=wo.get("hazard_desc", ""),
            clause=wo.get("clause", ""),
            requirement=wo.get("requirement", ""),
            risk_level=risk_level,
            worker_notice=action_payload.get("worker_notice", ""),
        )

        # 标记任务完成
        self.tasks.update_status(task_id, "completed")
