"""任务服务（M02 支撑）：任务创建、进度追踪、人工改判、结果持久化。

进度字典存内存（供页面轮询）；改判落 DB（risks 表）+ 写审计（C4）。
"""
from __future__ import annotations

import json
import sqlite3
import csv
import io
import threading

from dao.models import (
    TaskDAO, RiskDAO, DetectionDAO, ComplianceDAO, WorkOrderDAO,
    AgentRunDAO, FeedbackDAO, AlarmEventDAO, NotificationLogDAO,
)
from core.logging import get_logger
from services.permission_service import PermissionService

log = get_logger(__name__)


class TaskService:
    """任务生命周期服务。"""

    # 内存进度：task_id -> {agent: {status, cost_ms}}
    _progress: dict[str, dict] = {}
    # 任务属主登记（v0.8）：task_id -> user_id，进度/结果轮询按属主隔离
    _task_owners: dict[str, str] = {}
    # 测试注入口：后台异步研判的 Orchestrator 类（None=真实实现）
    _ORCH_FACTORY = None

    # 清空范围：仅业务数据；用户、审计日志、知识库与模型注册保留
    _CLEARABLE_TABLES = (
        "detection_records",
        "alarm_events",
        "notification_logs",
        "feedback_samples",
        "agent_runs",
        "work_orders",
        "risks",
        "compliances",
        "detections",
        "tasks",
    )

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.tasks = TaskDAO(conn)
        self.risks = RiskDAO(conn)
        self.detections = DetectionDAO(conn)
        self.compliances = ComplianceDAO(conn)
        self.work_orders = WorkOrderDAO(conn)
        self.agent_runs = AgentRunDAO(conn)
        self.feedback = FeedbackDAO(conn)
        self.alarms = AlarmEventDAO(conn)
        self.notifications = NotificationLogDAO(conn)
        self.permissions = PermissionService(conn)

    def create_task(self, user_id: str, files: list[str], permit_info: dict,
                    source: str = "upload") -> str:
        """创建任务，返回 task_id；写审计由调用方负责。

        source 标记输入来源（camera/upload/text），台账据此区分机器感知与人工上报。
        """
        self.permissions.require(user_id, "upload")
        tid = self.tasks.insert(user_id, json.dumps(permit_info, ensure_ascii=False),
                                "running", source=source)
        TaskService._progress[tid] = {}
        TaskService._task_owners[tid] = user_id or ""
        return tid

    # ---------- v0.6 上传链路异步化 ----------
    _async_running: dict[str, bool] = {}
    _async_results: dict[str, dict] = {}

    def start_async_run(self, task_id: str, user_id: str | None,
                        images: list[str], permit_info: dict,
                        scene_id: str = "hot_work") -> bool:
        """后台线程执行多 Agent 重链路，立即返回（进度经 update_progress 轮询）。

        完成/失败结果写 `_async_results[task_id]`（含 payload 或 error），
        页面 fragment 轮询到后展示。同一任务进行中重复启动返回 False。
        """
        self.permissions.require(user_id, "upload")
        if TaskService._async_running.get(task_id):
            return False
        # v0.8：属主校验——非本任务属主不可发起后台研判（防跨会话操纵他人任务）
        if not self._is_owner(task_id, user_id or ""):
            return False
        TaskService._async_running[task_id] = True
        svc_ref = self

        def _worker() -> None:
            try:
                from agents.orchestrator import Orchestrator
                cls = TaskService._ORCH_FACTORY or Orchestrator
                orch = cls(progress_cb=svc_ref.update_progress,
                                    scene_id=scene_id,
                                    work_order_dao=svc_ref.work_orders)
                result = orch.execute(task_id, images=images,
                                      permit_info=permit_info)
                svc_ref.save_result(task_id, result.payload)
                TaskService._async_results[task_id] = result.to_dict()
                wo = result.payload.get("work_order") or {}
                if getattr(orch, "action", None) is not None:
                    orch.action.polish(task_id, wo.get("hazard_desc", ""),
                                       wo.get("clause", ""),
                                       wo.get("requirement", ""),
                                       wo.get("deadline", ""))
            except Exception as exc:  # noqa: BLE001 失败也要落可读结果
                log.warning(f"后台研判任务 {task_id} 失败: {type(exc).__name__}: {exc}")
                TaskService._async_results[task_id] = {
                    "status": "failed", "error": f"{type(exc).__name__}: {exc}"}
            finally:
                TaskService._async_running.pop(task_id, None)

        threading.Thread(target=_worker, daemon=True).start()
        return True

    def pop_async_result(self, task_id: str,
                         user_id: str | None = None) -> dict | None:
        """页面轮询：取走完成结果（取后即清，避免驻留）。

        v0.8：已登记属主的任务校验 user_id，非属主取不到结果（返回 None）；
        user_id=None 视为内部/兼容调用，保持旧行为。
        """
        if user_id and not self._is_owner(task_id, user_id):
            return None
        return TaskService._async_results.pop(task_id, None)

    def create_text_hazard(self, user_id: str | None, description: str,
                           hazard_key: str, scene_id: str = "hot_work",
                           location: str | None = None) -> str:
        """文字线索直接建单（v0.4 P2-v0）：跳过视觉链路，按 severity 查级落库。

        白名单约束：hazard_key 必须命中 compliance.severity 且非 safe（安全正向
        信号不是隐患）——禁止用户/上层输入编造分类键。定级是纯规则查表。
        """
        self.permissions.require(user_id, "upload")
        from core.compliance import SEVERITY
        sev = SEVERITY.get(hazard_key)
        if sev is None:
            raise ValueError(f"未知隐患类别 {hazard_key}（须为既有隐患键白名单成员）")
        if sev == "safe":
            raise ValueError(f"{hazard_key} 为正向安全信号，不构成隐患上报项")
        risk_level = {"critical": "较大", "warning": "一般"}[sev]

        desc = description.strip()
        if not desc:
            raise ValueError("请填写隐患描述")
        if location and location.strip():
            desc = f"[{location.strip()}] {desc}"

        permit_info = {"scene": scene_id, "area": (location or "").strip(),
                       "report_type": "text"}
        tid = self.tasks.insert(user_id, json.dumps(permit_info, ensure_ascii=False),
                                "completed", source="text")
        TaskService._task_owners[tid] = user_id or ""
        # 复用处置 Agent 的等级话术模板，保持工单文案口径一致
        from agents.action_agent import ActionAgent
        agent = ActionAgent()
        notice_template = agent._template(desc, "", risk_level)
        requirement_line = next(
            (line for line in notice_template.splitlines()
             if line.startswith("整改要求")), "整改要求：限期整改。")
        self.risks.insert(tid, risk_level,
                          json.dumps([desc], ensure_ascii=False), "[]")
        self.work_orders.insert(
            task_id=tid, hazard_desc=desc, clause="文字上报无规范条款引用",
            requirement=requirement_line.replace("整改要求：", ""),
            risk_level=risk_level, worker_notice=notice_template)
        from dao.models import AuditDAO
        AuditDAO(self.conn).insert(
            user_id, "text_report",
            json.dumps({"task_id": tid, "cls": hazard_key, "scene": scene_id},
                       ensure_ascii=False))
        return tid

    def clear_all_data(self, user_id: str | None, confirmation: str) -> dict:
        """清空全部业务数据；保留账号、审计日志、知识库与模型注册。"""
        self.permissions.require(user_id, "clear_data")
        if confirmation.strip() != "RESET":
            raise ValueError("清空确认码必须为 RESET")

        counts: dict[str, int] = {}
        with self.conn:
            for table in self._CLEARABLE_TABLES:
                counts[table] = self.conn.execute(f"DELETE FROM {table}").rowcount
            TaskService._progress.clear()
            self.conn.execute(
                "INSERT INTO audit_logs(user_id, action, detail_json, created_at) "
                "VALUES(?,?,?,datetime('now'))",
                (user_id, "clear_data", json.dumps({"deleted": counts}, ensure_ascii=False)))
        return {"ok": True, "deleted": counts}

    def update_progress(self, task_id: str, agent: str, status: str, cost_ms: int = 0) -> None:
        """供 Orchestrator 回调，更新某 Agent 的进度。"""
        prog = TaskService._progress.setdefault(task_id, {})
        prog[agent] = {"status": status, "cost_ms": cost_ms}

    def get_progress(self, task_id: str, user_id: str | None = None) -> dict:
        """返回 {agent: {status, cost_ms}}。

        v0.8：已登记属主的任务校验 user_id，非属主视角返回空（防跨会话窥探）；
        user_id=None 视为内部/兼容调用，保持旧行为。
        """
        if user_id and not self._is_owner(task_id, user_id):
            return {}
        return dict(TaskService._progress.get(task_id, {}))

    @staticmethod
    def _is_owner(task_id: str, user_id: str) -> bool:
        """属主判定：未登记（旧数据/告警转单等链路）视为公开。"""
        owner = TaskService._task_owners.get(task_id)
        return owner is None or owner == user_id

    def list_agent_runs(self, task_id: str) -> list:
        """返回任务级 Agent 运行证据链。"""
        return self.agent_runs.list_by_task(task_id)

    def manual_override(self, task_id: str, new_level: str, reason: str,
                        user_id: str | None = None) -> bool:
        """人工改判风险等级（写审计在调用方）。"""
        self.permissions.require(user_id, "override")
        row = self.risks.get_by_task(task_id)
        if row is None:
            return False
        self.risks.override(row["id"], new_level, reason)
        return True

    def save_feedback_sample(
        self, task_id: str, user_id: str | None,
        corrected_level: str, reason: str,
        auto_level: str | None = None,
        source_json: dict | None = None,
        feedback_type: str = "override",
        image_path: str | None = None,
        detections: list[dict] | None = None,
        corrected_labels: list[dict] | None = None,
        status: str = "pending",
    ) -> str | None:
        """保存人工纠偏样本，构成人机纠偏闭环的反馈数据源。"""
        self.permissions.require(user_id, "override")
        return self.feedback.insert(
            task_id=task_id,
            user_id=user_id,
            auto_risk_level=auto_level,
            corrected_risk_level=corrected_level,
            reason=reason,
            feedback_type=feedback_type,
            source_json=json.dumps(source_json or {}, ensure_ascii=False),
            image_path=image_path,
            detection_json=json.dumps(detections or [], ensure_ascii=False),
            corrected_labels_json=json.dumps(corrected_labels or [], ensure_ascii=False),
            status=status,
        )

    def list_feedback_samples(self, limit: int = 500) -> list:
        """返回人工纠偏样本，供管理端查看。"""
        return self.feedback.list_all(limit=limit)

    def review_feedback_sample(self, feedback_id: str, status: str,
                               user_id: str | None = None) -> None:
        self.permissions.require(user_id, "override")
        self.feedback.update_review(feedback_id, status, user_id)

    def update_feedback_corrections(
        self,
        feedback_id: str,
        corrected_labels: list[dict],
        user_id: str | None = None,
    ) -> None:
        """更新已有反馈样本的逐目标修正结果。"""
        self.permissions.require(user_id, "override")
        self.feedback.update_corrections(
            feedback_id,
            json.dumps(corrected_labels, ensure_ascii=False),
            user_id,
        )

    def feedback_csv(self) -> str:
        """导出纠偏样本为 CSV，供后续训练/评估使用。

        含 status 列：不筛即完整审计流水；筛 status=confirmed 即已审核训练子集。
        """
        rows = self.feedback.list_all(limit=5000)
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            "created_at", "task_id", "user_id", "auto_risk_level",
            "corrected_risk_level", "reason", "feedback_type", "source_json",
            "status",
        ])
        for r in rows:
            writer.writerow([
                r["created_at"], r["task_id"], r["user_id"],
                r["auto_risk_level"], r["corrected_risk_level"],
                r["reason"], r["feedback_type"], r["source_json"],
                r["status"],
            ])
        return buf.getvalue()

    def create_alarm_event(self, session_id: str | None, task_id: str | None,
                           scene_id: str | None, cls: str | None,
                           conf: float | None, source: str | None = None,
                           image_path: str | None = None,
                           force: bool = False) -> str | None:
        """创建告警事件；默认同一会话同一类别已有未关闭告警则跳过。"""
        if not force and session_id and cls and self.alarms.find_open(session_id, cls):
            return None
        return self.alarms.insert(session_id, task_id, scene_id, cls, conf,
                                  image_path=image_path, source=source)

    def attach_alarm_image(self, alarm_id: str, image_path: str | None) -> None:
        """回填告警证据截图路径。"""
        self.alarms.set_image(alarm_id, image_path)

    def notify_alarm(self, alarm_id: str) -> None:
        """对单个告警发起异步外部推送（webhook/企业微信/钉钉）。"""
        from services.notify_service import NotificationService
        NotificationService().push_alarm_async(alarm_id)

    def raise_alarm(self, session_id: str | None, scene_id: str | None,
                    cls: str | None, conf: float | None,
                    source: str | None = None,
                    annotated_bgr=None, force: bool = False) -> str | None:
        """完整告警链路：创建告警 → 证据截图留存 → 回填 → 异步推送。

        返回告警 ID；同会话同类未关闭告警去重时返回 None。
        """
        aid = self.create_alarm_event(
            session_id, None, scene_id, cls, conf,
            source=source, force=force)
        if not aid:
            return None
        path = None
        if annotated_bgr is not None:
            try:
                from core.evidence import save_alarm_evidence
                path = save_alarm_evidence(session_id, cls, annotated_bgr)
            except Exception:  # noqa: BLE001 证据留存失败不应中断告警
                path = None
        if path:
            self.attach_alarm_image(aid, path)
        self.notify_alarm(aid)
        # 告警已落库+推送后，异步挂载规范条款（RAG 检索，无 LLM，不阻塞触发）
        self._attach_clause_async(aid, scene_id, cls)
        return aid

    def _attach_clause_async(self, alarm_id: str | None,
                             scene_id: str | None, cls: str | None) -> None:
        """告警已落库后异步挂载规范条款（RAG 检索，无 LLM，不阻塞告警触发）。

        实时链路的唯一 AI 增强：告警当帧已响，条款秒级异步回填到同一 alarm_event。
        决策段（detect→filter→track→evaluate→raise）仍是纯规则，此处不进任何 LLM。
        检索侧加文档无关噪音过滤：丢弃含 URL / 数字标点占比过高 / 过短的块，取剩余最高分。
        """
        def _worker() -> None:
            try:
                if not alarm_id or not scene_id or not cls:
                    return
                from core.config import ConfigLoader
                from core.rag_engine import RagEngine
                from core.yolo_engine import WHITELIST_CN
                kb = (ConfigLoader().get_scene(scene_id) or {}).get("kb_collection")
                if not kb:
                    return
                desc = WHITELIST_CN.get(cls)
                query = f"{desc} 不合规" if desc else "动火作业安全规范"
                rows = RagEngine(collection_name=kb).query(query, top_k=5)
                top = TaskService._pick_clause(rows)
                if not top:
                    return
                no, text = top.get("clause_no", ""), top.get("clause_text", "")
                clause = (f"第{no}条 {text}".strip()) if no else text
                if clause:
                    self.alarms.update_clause(alarm_id, clause)
            except Exception as exc:  # noqa: BLE001 条款挂载失败不影响告警，但留痕
                log.warning(f"告警 {alarm_id} 条款挂载失败: {exc}")

        threading.Thread(target=_worker, daemon=True).start()

    @staticmethod
    def _pick_clause(rows: list[dict]) -> dict | None:
        """从 RAG 检索结果中剔除文档无关噪音，返回剩余最高分条款。

        噪音判定：含 URL、数字/标点占比>60%、正文<8 字或无中文——通常是页眉页脚残片。
        """
        import re
        best: dict | None = None
        for r in rows:
            text = (r.get("clause_text") or "").strip()
            if len(text) < 8:
                continue
            if "http://" in text or "https://" in text:
                continue
            if not re.search(r"[一-鿿]", text):
                continue
            noise = len(re.findall(r"[\d\s，。、；：！？.\/:;,.!?]", text))
            if noise / len(text) > 0.6:
                continue
            if best is None or (r.get("score", 0.0) > best.get("score", 0.0)):
                best = r
        return best

    def list_notification_logs(self, limit: int = 200) -> list:
        return self.notifications.list_all(limit=limit)

    def list_alarm_events(self, limit: int = 500) -> list:
        return self.alarms.list_all(limit=limit)

    def update_alarm_event(self, alarm_id: str, status: str,
                           user_id: str | None = None) -> None:
        self.permissions.require(user_id, "override")
        self.alarms.update_status(alarm_id, status, user_id)

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
                "clause_no": c.get("clause_no") or c.get("clause_ref") or "",
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

        # 5) Agent 运行证据链：展示多 Agent 协同、耗时与输出摘要
        self.save_agent_runs(task_id, agent_results)

        # 标记任务完成
        self.tasks.update_status(task_id, "completed")

    def save_agent_runs(self, task_id: str, agent_results: dict) -> None:
        """持久化各 Agent 的执行轨迹，供证据链追溯与答辩演示使用。"""
        rows: list[dict] = []
        for agent, node in (agent_results or {}).items():
            if not isinstance(node, dict):
                continue
            rows.append({
                "task_id": task_id,
                "agent": agent,
                "status": node.get("status", "unknown"),
                "cost_ms": node.get("cost_ms", 0),
                "input_json": json.dumps(
                    self._summarize_input(node.get("payload")),
                    ensure_ascii=False),
                "output_json": json.dumps(
                    self._summarize_output(node.get("payload")),
                    ensure_ascii=False),
                "error": node.get("error"),
            })
        if rows:
            self.agent_runs.bulk_insert(rows)

    @staticmethod
    def _summarize_input(payload) -> dict:
        """提取 Agent 输出中保留的输入摘要。"""
        if not isinstance(payload, dict):
            return {}
        return payload.get("input_summary", {}) or {}

    @staticmethod
    def _summarize_output(payload) -> dict:
        """将 Agent 输出压缩为轻量摘要，避免把大段检测坐标写入证据链。"""
        if not isinstance(payload, dict):
            return {"summary": str(payload)[:300]}
        summary: dict = {}
        if isinstance(payload.get("detections"), list):
            summary["detections"] = [
                {"cls": d.get("cls"), "conf": d.get("conf")}
                for d in payload["detections"][:50]
            ]
        if isinstance(payload.get("compliance"), list):
            summary["compliance"] = [
                {
                    "label": c.get("label"),
                    "verdict": c.get("verdict"),
                    "clause_ref": c.get("clause_ref") or c.get("clause_no") or "",
                }
                for c in payload["compliance"][:50]
            ]
        if isinstance(payload.get("work_order"), dict):
            wo = payload["work_order"]
            summary["work_order"] = {
                "risk_level": wo.get("risk_level"),
                "hazard_desc": wo.get("hazard_desc"),
                "clause": wo.get("clause"),
                "requirement": wo.get("requirement"),
            }
        for key in ("risk_level", "reasons", "training_tips",
                    "filtered_fp", "worker_notice", "fire_model_limitation",
                    "needs_review", "review_reasons"):
            if key in payload:
                value = payload[key]
                if isinstance(value, str):
                    summary[key] = value[:500]
                else:
                    summary[key] = value
        return summary or {"keys": list(payload.keys())[:20]}
