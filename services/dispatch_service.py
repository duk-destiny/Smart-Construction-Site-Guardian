"""工单派发与整改验收服务（v0.2 P0 工单闭环）。

四件事：①按 `config.yaml` 的 `dispatch.rules`（数据驱动、自上而下首中即用）
把工单指派给 responsible 责任人，截止时间按风险等级查表取默认值；
②责任人提交整改说明与现场照片；③安全员/管理员验收通过或驳回；
④`scan_overdue` 纯函数逾期巡检——演示走管理端按钮 + 时间游标手动触发，
生产由 `scripts/overdue_scan.py` 挂系统 cron 驱动同一函数。

设计铁律（与 README Q3/Q6 一致）：派发查确定性映射、时限查表、全程审计落库，
LLM 不参与任何判定路径。外部推送当前仅写审计流水（notify webhook 与告警事件
绑定），webhook 化催办列入二期。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from dao.models import AuditDAO, UserDAO, WorkOrderDAO
from services.permission_service import AuthorizationError
from services.permission_service import PermissionService

# 各风险等级的默认整改时限（小时）；"低"亦给一周余量便于台账统计，
# 现场可派发时手动覆盖。与处置 Agent 的 _DEADLINES 文案口径对齐：
# 重大→立即(收紧为1h)/较大→2h/一般→24h。
RISK_DEADLINE_HOURS = {"重大": 1, "较大": 2, "一般": 24, "低": 168}


def _now_str(offset_hours: float = 0.0) -> str:
    """UTC 时间串，与 SQLite ``datetime('now')`` 同口径。

    统一 UTC 才能让 deadline 比较和演示"时间游标"(offset_hours)保持确定语义。
    """
    return (datetime.now(timezone.utc) + timedelta(hours=offset_hours)).strftime(
        "%Y-%m-%d %H:%M:%S")


def _hours_between(later: str, earlier: str) -> float:
    """两个 'YYYY-MM-DD HH:MM:SS' 串的小时差（later-earlier）。"""
    fmt = "%Y-%m-%d %H:%M:%S"
    delta = datetime.strptime(later[:19], fmt) - datetime.strptime(earlier[:19], fmt)
    return delta.total_seconds() / 3600.0


class DispatchService:
    """派发 / 整改提交 / 验收 / 逾期巡检。"""

    def __init__(self, conn: sqlite3.Connection, rules: list[dict] | None = None,
                 notifier=None) -> None:
        self.conn = conn
        self.users = UserDAO(conn)
        from dao.models import RiskDAO
        self.risks = RiskDAO(conn)
        self.orders = WorkOrderDAO(conn)
        self.audit = AuditDAO(conn)
        self.permissions = PermissionService(conn)
        # v0.8 派发提醒：注入 fake 供测试（同步调用）；None=真实服务（daemon 异步）
        self._notifier = notifier
        if rules is None:
            from core.config import ConfigLoader
            conf = ConfigLoader().get("dispatch") or {}
            rules = conf.get("rules") or []
        self.rules = [r for r in rules if isinstance(r, dict)]

    # ---------- 规则解析 ----------
    def resolve_assignee(self, scene_id: str | None = None,
                         zone: str | None = None) -> str | None:
        """返回建议责任人用户名（首中即用）；无命中返回 None 转手动指派。

        规则字段：scene/zone 可省略视为通配；均需命中才采用该条。
        """
        for rule in self.rules:
            if not isinstance(rule, dict):
                continue
            rule_scene = rule.get("scene")
            if rule_scene is not None and scene_id is not None \
                    and rule_scene != scene_id:
                continue
            rule_zone = rule.get("zone")
            if rule_zone is not None and zone is not None and rule_zone != zone:
                continue
            assignee = rule.get("assignee")
            if assignee:
                return str(assignee)
        return None

    def _responsible_id(self, username: str | None) -> str | None:
        """用户名 → users.id；校验必须是 responsible 角色。"""
        if not username:
            return None
        row = self.users.get_by_name(username)
        if row is None:
            raise ValueError(f"指派对象 {username} 不存在")
        if row["role"] != "responsible":
            raise ValueError(f"{username} 不是整改责任人（responsible）账号")
        return row["id"]

    # ---------- 派发 ----------
    def dispatch_order(self, task_id: str, actor_user_id: str | None,
                       assignee_username: str | None = None,
                       deadline_hours: float | None = None,
                       scene_id: str | None = None,
                       zone: str | None = None) -> str:
        """派发（或改派）工单并写审计，返回工单 ID。

        未显式指定责任人时按规则解析；仍无命中报错转人工。
        仅 open/rejected 状态可派发；submitted 待验收不可抢改。
        """
        self.permissions.require(actor_user_id, "override")
        order = self.orders.get_by_task(task_id)
        if order is None:
            raise ValueError("该任务尚未生成工单")
        if order["status"] not in ("open", "rejected"):
            raise ValueError(f"工单状态为 {order['status']}，不允许派发/改派")

        username = assignee_username or self.resolve_assignee(scene_id, zone)
        uid = self._responsible_id(username)
        if uid is None:
            raise ValueError("未匹配派发规则且未指定责任人，请手动选择")

        if deadline_hours is None:
            deadline_hours = RISK_DEADLINE_HOURS.get(order["risk_level"], 24)
        now = _now_str()
        deadline = _now_str(float(deadline_hours))
        self.orders.set_dispatch(order["id"], uid, deadline, now)
        self.audit.insert(actor_user_id, "dispatch", json.dumps({
            "order_id": order["id"], "task_id": task_id,
            "assignee": username, "deadline": deadline,
        }, ensure_ascii=False))
        # v0.8 派发即推送责任人：不等逾期，派单当下即送达（notify 未启用自动 skipped）
        self._notify_dispatch(order["id"], username, order["hazard_desc"],
                              deadline, order["risk_level"])
        return order["id"]

    def _notify_dispatch(self, order_id: str, assignee: str | None,
                         hazard: str, deadline: str, risk_level: str) -> None:
        """派发提醒推送：注入 notifier（测试）走同步；真实服务走 daemon 线程不阻塞 UI。"""
        try:
            if self._notifier is not None:
                self._notifier.push_dispatch(order_id, assignee, hazard,
                                             deadline, risk_level)
                return
            import threading
            from services.notify_service import NotificationService
            svc = NotificationService()
            threading.Thread(
                target=svc.push_dispatch,
                args=(order_id, assignee, hazard, deadline, risk_level),
                daemon=True).start()
        except Exception as exc:  # noqa: BLE001 提醒失败不影响派发，但留痕
            from core.logging import get_logger
            get_logger(__name__).warning(f"工单 {order_id} 派发提醒推送失败: {exc}")

    # ---------- 告警→工单桥（轻链路产物进派发闭环）----------
    def convert_alarm_to_order(self, alarm_id: str,
                               actor_user_id: str | None) -> str:
        """把实时高危告警转为整改工单（读写隔离：仅显式按钮触发）。

        task.source='camera'；风险等级按 severity 查表映射
        （critical→较大 / warning→一般，保守不入"重大"——该档留给人工改判）；
        告警状态同步置 confirmed 并在审计中互相引用。
        """
        from core.compliance import SEVERITY
        from core.yolo_engine import WHITELIST_CN
        from dao.models import AlarmEventDAO, TaskDAO

        self.permissions.require(actor_user_id, "override")
        conn = self.conn
        alarm = AlarmEventDAO(conn).get_by_id(alarm_id)
        if alarm is None:
            raise ValueError("告警不存在")
        if alarm["status"] not in ("new", "confirmed"):
            raise ValueError(f"告警状态为 {alarm['status']}，不可转为工单")
        # 幂等守卫以审计流水为准：实时告警 task_id 恒为 None,
        # 不可篡改的 audit_logs 是唯一可信的"已转换"凭证（audit 仅追加）
        dup = conn.execute(
            "SELECT 1 FROM audit_logs WHERE action='alarm_to_order' "
            "AND detail_json LIKE ? LIMIT 1",
            (f'%"alarm_id": "{alarm_id}"%',)).fetchone()
        if dup:
            raise ValueError("该告警已转为工单，请勿重复转换")

        cls = alarm["cls"]
        sev = SEVERITY.get(cls or "", "warning")
        risk_level = {"critical": "较大", "warning": "一般"}.get(sev, "一般")
        desc_cn = WHITELIST_CN.get(cls or "", cls or "未知隐患")
        location = f"（来源 {alarm['source'] or 'rtsp'}）"
        desc = f"[实时告警] {desc_cn} {location}".strip()

        # Phase 1 事务化：五段写挂起逐段提交，末尾单次 commit；
        # 中途失败整体回滚，避免"告警已 confirmed 却无工单"的悬空态
        try:
            tid = TaskDAO(conn).insert(
                actor_user_id,
                json.dumps({"scene": alarm["scene_id"],
                            "report_type": "alarm", "alarm_id": alarm_id},
                           ensure_ascii=False),
                "completed", source="camera", commit=False)
            from agents.action_agent import ActionAgent
            notice_template = ActionAgent()._template(desc, alarm["clause"] or "",
                                                     risk_level)
            requirement_line = next(
                (line for line in notice_template.splitlines()
                 if line.startswith("整改要求")),
                "整改要求：限期整改。").replace("整改要求：", "")
            self.risks.insert(tid, risk_level, json.dumps([desc], ensure_ascii=False),
                              "[]", commit=False)
            order_id = self.orders.insert(
                task_id=tid, hazard_desc=desc, clause=alarm["clause"] or "",
                requirement=requirement_line, risk_level=risk_level,
                worker_notice=notice_template, commit=False)
            AlarmEventDAO(conn).update_status(alarm_id, "confirmed", actor_user_id,
                                              commit=False)
            self.audit.insert(actor_user_id, "alarm_to_order", json.dumps({
                "alarm_id": alarm_id, "task_id": tid, "order_id": order_id,
                "cls": cls, "risk_level": risk_level,
            }, ensure_ascii=False), commit=False)
            conn.commit()
            return order_id
        except Exception:
            conn.rollback()
            raise

    # ---------- 整改提交 ----------
    def submit_rectification(self, order_id: str, user_id: str | None,
                             note: str, image_paths: list[str] | None = None) -> None:
        """责任人提交整改说明（照片已先行落盘为相对路径列表）。"""
        self.permissions.require(user_id, "rectify")
        order = self.orders.get(order_id)
        if order is None:
            raise ValueError("工单不存在")
        if order["status"] not in ("open", "rejected"):
            raise ValueError(f"工单状态为 {order['status']}，无法提交整改")
        if order["assignee_id"] != user_id:
            raise PermissionError("仅本单责任人可提交整改")
        if not note or not note.strip():
            raise ValueError("请填写整改说明")
        self.orders.set_submitted(
            order_id, note.strip(),
            json.dumps(image_paths or [], ensure_ascii=False))
        self.audit.insert(user_id, "rectification_submit", json.dumps({
            "order_id": order_id, "task_id": order["task_id"],
            "images": len(image_paths or []),
        }, ensure_ascii=False))

    # ---------- 验收 ----------
    def review_order(self, order_id: str, reviewer_user_id: str | None,
                     approve: bool, reason: str = "") -> None:
        """验收：通过销项；驳回退回 open 并留原因供责任人再改。"""
        self.permissions.require(reviewer_user_id, "override")
        order = self.orders.get(order_id)
        if order is None:
            raise ValueError("工单不存在")
        if order["status"] != "submitted":
            raise ValueError(f"工单状态为 {order['status']}，不在待验收队列")
        if not approve and not reason.strip():
            raise ValueError("驳回必须填写原因")
        self.orders.set_reviewed(order_id, approve, reviewer_user_id,
                                 reason.strip())
        self.audit.insert(reviewer_user_id, "review", json.dumps({
            "order_id": order_id, "task_id": order["task_id"],
            "approve": approve, "reason": reason.strip(),
        }, ensure_ascii=False))

    # ---------- 逾期巡检 ----------
    def scan_overdue(self, as_of: str | None = None,
                     escalate_after_hours: float = 24.0,
                     notifier=None) -> dict:
        """扫描逾期未销项工单：审计流水 + webhook 催办推送（可注入便于测试）。

        每单两档推送：责任人催办；逾期满 escalate_after_hours 追加越级升级
        （收件语义为管理层）。notify 未启用时推送自动 skipped（留痕），
        审计流水不受影响。`notifier` 供测试注入 fake。

        每单设冷却窗口（取 notifier.cooldown_sec），同一订单在窗口内不重复推送，
        避免 cron 高频触发导致消息轰炸。
        """
        if notifier is None:
            from services.notify_service import NotificationService
            notifier = NotificationService()
        now = as_of or _now_str()
        rows = self.orders.list_overdue(now)
        cooldown_sec = getattr(notifier, "cooldown_sec", lambda: 60)()
        notified = escalated = 0
        push_sent = push_skipped = push_failed = 0
        cooldown_skipped = 0
        for row in rows:
            if self._recently_notified(row["id"], now, cooldown_sec):
                cooldown_skipped += 1
                continue
            overdue_h = max(0.0, _hours_between(now, row["deadline"] or now))
            base = {
                "order_id": row["id"], "task_id": row["task_id"],
                "assignee_id": row["assignee_id"], "deadline": row["deadline"],
                "overdue_hours": round(overdue_h, 1),
            }
            self.audit.insert(None, "overdue_notify", json.dumps(base, ensure_ascii=False))
            notified += 1
            res = notifier.push_overdue(
                row["id"], base["assignee_id"], row["hazard_desc"],
                row["deadline"], overdue_h, escalate=False)
            push_sent += res.get("status") == "sent"
            push_skipped += res.get("status") == "skipped"
            push_failed += res.get("status") == "failed"
            if overdue_h >= escalate_after_hours:
                esc = dict(base, level="admin")
                self.audit.insert(None, "overdue_escalate",
                                  json.dumps(esc, ensure_ascii=False))
                escalated += 1
                res2 = notifier.push_overdue(
                    row["id"], base["assignee_id"], row["hazard_desc"],
                    row["deadline"], overdue_h, escalate=True)
                push_sent += res2.get("status") == "sent"
                push_skipped += res2.get("status") == "skipped"
                push_failed += res2.get("status") == "failed"
        return {"as_of": now, "overdue": len(rows),
                "notified": notified, "escalated": escalated,
                "cooldown_skipped": cooldown_skipped,
                "push_sent": push_sent, "push_skipped": push_skipped,
                "push_failed": push_failed}

    def _recently_notified(self, order_id: str, as_of: str,
                           cooldown_sec: float) -> bool:
        """检查该工单在 cooldown_sec 内是否已有催办审计记录。"""
        cutoff = (datetime.strptime(as_of[:19], "%Y-%m-%d %H:%M:%S")
                  - timedelta(seconds=cooldown_sec)).strftime("%Y-%m-%d %H:%M:%S")
        row = self.conn.execute(
            "SELECT 1 FROM audit_logs WHERE action='overdue_notify' "
            "AND detail_json LIKE ? AND created_at > ? LIMIT 1",
            (f'%"order_id": "{order_id}"%', cutoff)).fetchone()
        return row is not None
