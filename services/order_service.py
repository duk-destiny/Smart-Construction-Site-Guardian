"""工单操作门面（Phase 0）：报告页派发/改判/导出与责任人整改提交的入口。

整改照片落盘（core.evidence）在此收口——原实现在 UI 层直呼 core。
审计行为与既有口径逐一对齐（dispatch_ui / override / detection_fix 等）。
"""
from __future__ import annotations

import json

from core.evidence import save_rectification_photo
from services.db import scoped
from services.dispatch_service import DispatchService, RISK_DEADLINE_HOURS
from services.permission_service import AuthorizationError
from services.task_service import TaskService


def dispatch_panel(task_id: str, scene_id: str = "hot_work") -> dict | None:
    """派发面板数据：工单行 + 责任人名 + responsible 候选 + 规则建议。"""
    from dao.models import UserDAO
    with scoped() as conn:
        svc = DispatchService(conn)
        wo = svc.orders.get_by_task(task_id)
        if wo is None:
            return None
        row = dict(wo)
        assignee_name = None
        if row["assignee_id"]:
            u = UserDAO(conn).get_by_id(row["assignee_id"])
            assignee_name = u["username"] if u else str(row["assignee_id"])
        names = [u["username"] for u in UserDAO(conn).list_by_role("responsible")]
        suggestion = svc.resolve_assignee(scene_id=scene_id)
    return {
        "order": row,
        "assignee_name": assignee_name,
        "responsible_names": names,
        "suggestion": suggestion,
        "default_hours": RISK_DEADLINE_HOURS.get(row["risk_level"], 24),
    }


def dispatch_order(task_id: str, actor_user_id: str | None,
                   assignee: str, hours: float,
                   scene_id: str = "hot_work") -> tuple[bool, str]:
    """派发/改派（服务层鉴权+审计），返回 (ok, 消息)。"""
    with scoped() as conn:
        svc = DispatchService(conn)
        try:
            oid = svc.dispatch_order(task_id, actor_user_id,
                                     assignee_username=assignee,
                                     deadline_hours=float(hours),
                                     scene_id=scene_id)
        except AuthorizationError as exc:  # 服务层自定义权限异常
            return False, f"权限不足：{exc}"
        except ValueError as exc:
            return False, str(exc)
        from services.audit_service import AuditService
        AuditService(conn).append(actor_user_id, "dispatch_ui",
                                  {"task_id": task_id})
    return True, f"已派发给 {assignee}（工单 {oid}）"


def submit_override(task_id: str, user_id: str | None, new_level: str,
                    reason: str, image_path: str | None = None,
                    detections: list[dict] | None = None) -> tuple[bool, str]:
    """人工改判 + 纠偏样本落库 + 审计（原报告页三段逻辑收敛）。"""
    with scoped() as conn:
        svc = TaskService(conn)
        ok = svc.manual_override(task_id, new_level, reason, user_id=user_id)
        if not ok:
            return False, "未找到该任务风险记录"
        risk = svc.risks.get_by_task(task_id)
        svc.save_feedback_sample(
            task_id=task_id,
            user_id=user_id,
            corrected_level=new_level,
            reason=reason,
            auto_level=risk["risk_level"] if risk else None,
            source_json={
                "reasons_json": risk["reasons_json"] if risk else None,
                "filtered_fp_json": risk["filtered_fp_json"] if risk else None,
            },
            image_path=image_path,
            detections=detections or [],
            corrected_labels=[{"risk_level": new_level, "reason": reason}],
        )
        from services.audit_service import AuditService
        AuditService(conn).append(user_id, "override",
                                  {"task_id": task_id, "level": new_level,
                                   "reason": reason})
    return True, "改判已记录"


def save_detection_fix(task_id: str, user_id: str | None, risk_level: str,
                       image_path: str | None, detections: list[dict],
                       corrections: list[dict]) -> None:
    """逐目标纠偏保存为待审核反馈样本 + 审计。"""
    with scoped() as conn:
        svc = TaskService(conn)
        svc.save_feedback_sample(
            task_id=task_id,
            user_id=user_id,
            corrected_level=risk_level,
            reason="逐目标纠偏",
            auto_level=risk_level,
            feedback_type="detection_fix",
            image_path=image_path,
            detections=detections,
            corrected_labels=corrections,
        )
        from services.audit_service import AuditService
        AuditService(conn).append(user_id, "detection_fix",
                                  {"task_id": task_id,
                                   "items": len(corrections)})


def export_excel(task_id: str, user_id: str | None) -> tuple[bool, str]:
    from services.export_service import ExportService
    with scoped() as conn:
        r = ExportService(conn).export_excel(task_id=task_id, user_id=user_id)
    if r.get("ok"):
        return True, r["data"]["file_path"]
    return False, "导出失败"


def export_order_excel(order_id: str, user_id: str | None) -> tuple[bool, str]:
    """按工单（而非任务）导出台账 Excel：order_id → task_id 转换。"""
    with scoped() as conn:
        wo = DispatchService(conn).orders.get(order_id)
    if wo is None:
        return False, "工单不存在"
    return export_excel(wo["task_id"], user_id)


# ---------- 责任人（responsible）侧 ----------

def my_orders(user_id: str) -> list[dict]:
    with scoped() as conn:
        rows = DispatchService(conn).orders.list_by_assignee(user_id)
        return [dict(r) for r in rows]


def submit_rectification(order_id: str, user_id: str | None, note: str,
                         photos: list) -> tuple[bool, str]:
    """责任人提交整改：照片先落盘（消毒+BASE_DIR 锚点）再进服务校验。"""
    paths: list[str] = []
    for f in photos or []:
        rel = save_rectification_photo(order_id, f.name, f.getvalue())
        if rel:
            paths.append(rel)
    with scoped() as conn:
        svc = DispatchService(conn)
        try:
            svc.submit_rectification(order_id, user_id, note, paths)
        except AuthorizationError as exc:
            return False, f"权限不足：{exc}"
        except ValueError as exc:
            return False, str(exc)
        from services.audit_service import AuditService
        AuditService(conn).append(user_id, "rectification_submit_view",
                                  {"order_id": order_id, "images": len(paths)})
    return True, "已提交验收 ✅ 请等待复核结果"


# ---------- 管理端验收/巡检 ----------

def pending_review_orders() -> list[dict]:
    """待验收队列（含责任人用户名与整改照片存在性标注）。"""
    import os
    from dao.models import UserDAO
    from core.paths import resolve
    with scoped() as conn:
        rows = DispatchService(conn).orders.list_by_status("submitted")
        out = []
        for r in rows:
            d = dict(r)
            if d["assignee_id"]:
                u = UserDAO(conn).get_by_id(d["assignee_id"])
                d["assignee_name"] = u["username"] if u else str(d["assignee_id"])
            else:
                d["assignee_name"] = None
            try:
                imgs = json.loads(d["submitted_imgs"] or "[]")
            except ValueError:
                imgs = []
            d["submitted_img_paths"] = [p for p in imgs if os.path.exists(resolve(p))]
            out.append(d)
    return out


def review_order(order_id: str, reviewer_user_id: str | None,
                 approve: bool, reason: str = "") -> tuple[bool, str]:
    with scoped() as conn:
        svc = DispatchService(conn)
        try:
            svc.review_order(order_id, reviewer_user_id, approve, reason)
        except AuthorizationError as exc:
            return False, f"权限不足：{exc}"
        except ValueError as exc:
            return False, str(exc)
    return True, ("已通过并关闭工单" if approve else "已驳回，退回责任人整改")


def scan_overdue(as_of: str, hours_ahead: float = 0.0) -> dict:
    with scoped() as conn:
        return DispatchService(conn).scan_overdue(as_of=as_of)


def ask_order(order_id: str, user_id: str, question: str) -> dict:
    """工单 AI 弹窗（v2.2）：仅携带当前工单上下文的只读问答。

    权限：本单责任人本人或 admin/safety；跨属主 403、不存在 404。
    实现：单轮 ChatClient（不进 Plan-and-Execute 内核、零工具调用、
    零写入），上下文=工单行+风险等级+要求条款+RAG 命中条款；LLM 全链
    不可用时降级为可读提示（不编造、不断链路）。
    """
    from dao.models import RiskDAO, UserDAO, WorkOrderDAO
    with scoped() as conn:
        wo = WorkOrderDAO(conn).get(order_id)
        if wo is None:
            raise LookupError("工单不存在")
        row = dict(wo)
        u = UserDAO(conn).get_by_id(user_id)
        role = u["role"] if u else ""
        if role not in ("admin", "safety") and row.get("assignee_id") != user_id:
            raise PermissionError("仅本单责任人或管理员可问询")
        risk = RiskDAO(conn).get_by_task(row["task_id"])
        ctx = {
            "risk_level": (risk["risk_level"] if risk else None)
            or row.get("risk_level"),
            "hazard_desc": row.get("hazard_desc"),
            "clause": row.get("clause"),
            "requirement": row.get("requirement"),
            "status": row.get("status"),
            "deadline": row.get("deadline"),
            "submitted_note": row.get("submitted_note"),
            "review_reason": row.get("review_reason"),
        }
    # RAG 条款检索（只读，失败降级为空集不阻断）
    clauses: list[str] = []
    try:
        from core.rag_engine import RagEngine
        items = RagEngine().query(question, top_k=3)
        clauses = [str(it) for it in (items or [])][:3]
    except Exception:  # noqa: BLE001 检索不可用即不带条款作答
        clauses = []

    from core.chat_client import get_chat_client
    client = get_chat_client()
    provider = client.available_provider()
    if not provider:
        return {"status": "failed",
                "answer": "AI 问答通道当前不可用（未配置或断网）。"
                          "可先阅读工单原文的条款与整改要求，稍后再试。"}
    system = (
        "你是工地整改工单的读单助手。只基于【工单上下文】与【参考条款】"
        "回答用户问题，回答限于：规范依据解释、整改要求解读、验收要点咨询；"
        "不得编造上下文与条款里没有的内容，不得改变风险定级，"
        "不回答与该工单无关的问题。不超过 250 字，分点作答。")
    ctx_text = json.dumps(ctx, ensure_ascii=False)
    clause_text = json.dumps(clauses, ensure_ascii=False)
    user_msg = (f"【工单上下文】{ctx_text}\n\n"
                f"【参考条款】{clause_text}\n\n"
                f"【用户问题】{question}")
    result = client.chat(system, user_msg, max_tokens=600,
                         total_deadline_sec=20.0)
    if result.status == "failed" or not (result.content or "").strip():
        return {"status": "failed", "provider": result.provider,
                "answer": "本次问答未能完成（AI 通道繁忙或超时），请稍后重试。"}
    return {"status": result.status, "provider": result.provider,
            "answer": str(result.content).strip()}
