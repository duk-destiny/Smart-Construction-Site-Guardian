"""风险分级周报服务（v0.3，P1 复盘层）。

聚合周期内三类事实源——检测帧(detection_records)、告警(alarm_events)、
工单闭环(work_orders 含责任人与逾期态)——输出结构化 stats 与结论行，
再以 fpdf2 渲染中文 PDF。同一 `gather()` 同时供：
① 管理端「风险周报」区块预览/下载；② `scripts/weekly_report.py`（cron）落盘归档。

铁律延续：统计全部为确定性 SQL 聚合，结论行为规则拼接，无 LLM 参与；
权限走既有 `export` 动作；文件落 `data/exports/`（.gitignore 已忽略）。
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime

from dao.models import AuditDAO
from services.permission_service import PermissionService


def _day_end(date_str: str) -> str:
    """'2030-01-10' → '2030-01-10 23:59:59'（含当日全天）。"""
    return f"{date_str} 23:59:59" if len(date_str) == 10 else date_str


class WeeklyReportService:
    """周期安全周报：gather(纯查询) → render_pdf(纯渲染) → generate(编排+审计)。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.audit = AuditDAO(conn)
        self.permissions = PermissionService(conn)

    # ---------- 聚合 ----------
    def gather(self, start: str | None = None, end: str | None = None) -> dict:
        """返回周期统计字典；start/end 为 ISO 日期字符串。"""
        params: list[str] = []
        if start:
            params = [start, _day_end(end or start)]

        # —— 检测概览 ——
        det_sql = ("SELECT COUNT(*) AS n, "
                   "SUM(CASE WHEN frame_status='不合规' THEN 1 ELSE 0 END) AS bad, "
                   "SUM(CASE WHEN frame_status='警告' THEN 1 ELSE 0 END) AS warn, "
                   "SUM(CASE WHEN frame_status='合规' THEN 1 ELSE 0 END) AS ok "
                   "FROM detection_records")
        if params:
            det_sql += " WHERE created_at >= ? AND created_at <= ?"
        det = self.conn.execute(det_sql, params).fetchone()

        top_sql = ("SELECT cls, COUNT(*) AS cnt FROM detection_records "
                   "WHERE cls <> 'none'")
        if params:
            top_sql += " AND created_at >= ? AND created_at <= ?"
        top_rows = self.conn.execute(top_sql + " GROUP BY cls ORDER BY cnt DESC LIMIT 5",
                                     params).fetchall()

        # —— 告警概况 ——
        alm_sql = ("SELECT status, COUNT(*) AS n FROM alarm_events")
        if params:
            alm_sql += " WHERE created_at >= ? AND created_at <= ?"
        alm_rows = self.conn.execute(alm_sql + " GROUP BY status", params).fetchall()
        alarms_by_status = {r["status"]: r["n"] for r in alm_rows}

        # —— 工单闭环（本报告重点，v0.2 数据）——
        wo_sql = ("SELECT id, status, risk_level, assignee_id, deadline "
                  "FROM work_orders")
        if params:
            wo_sql += " WHERE created_at >= ? AND created_at <= ?"
        wo_rows = self.conn.execute(wo_sql + " ORDER BY created_at DESC",
                                    params).fetchall()
        wo_by_status = {"open": 0, "submitted": 0, "closed": 0, "rejected": 0}
        for r in wo_rows:
            wo_by_status[r["status"]] = wo_by_status.get(r["status"], 0) + 1

        # 当前存量视角：所有未销项工单按截止时间对照报告期末尾判逾期
        as_of = _day_end(end or start or "")
        overdue_ids = {
            r["id"] for r in self.conn.execute(
                "SELECT id, assignee_id FROM work_orders "
                "WHERE status='open' AND deadline IS NOT NULL AND deadline < ?",
                (as_of,))
        }

        # 按责任人汇总（覆盖全量未销项 + 周期内已销项）
        per_assignee_sql = """
            SELECT u.username AS name,
                   COUNT(*)                                        AS assigned,
                   SUM(CASE WHEN w.status='closed' THEN 1 ELSE 0 END)   AS closed_n,
                   SUM(CASE WHEN w.status='submitted' THEN 1 ELSE 0 END) AS submitted_n,
                   SUM(CASE WHEN w.status IN ('open','rejected') THEN 1 ELSE 0 END) AS active_n
            FROM work_orders w JOIN users u ON u.id = w.assignee_id
        """
        per_params: list = []
        if params:
            per_assignee_sql += " WHERE w.created_at >= ? AND w.created_at <= ?"
            per_params = list(params)
        per_assignee_sql += " GROUP BY u.id ORDER BY assigned DESC"
        per_assignee = []
        for r in self.conn.execute(per_assignee_sql, per_params).fetchall():
            od = self.conn.execute(
                "SELECT COUNT(*) AS n FROM work_orders w, users u "
                "WHERE u.id=w.assignee_id AND u.username=? AND w.status='open' "
                "AND w.deadline IS NOT NULL AND w.deadline < ?",
                (r["name"], as_of)).fetchone()["n"]
            item = dict(r)
            item["overdue_n"] = od
            item["overdue_rate"] = round(od / r["assigned"], 3) if r["assigned"] else 0.0
            per_assignee.append(item)

        # —— 规则化结论行（无 LLM）——
        total_frames = int(det["n"] or 0)
        bad = int(det["bad"] or 0)
        closed = wo_by_status["closed"]
        total_wo = len(wo_rows)
        conclusions: list[str] = []
        if total_wo:
            rate = round(closed / total_wo * 100, 1)
            conclusions.append(f"周期内新增工单 {total_wo} 张，已销项 {closed} 张"
                               f"（销项率 {rate}%）。")
        if overdue_ids:
            conclusions.append(f"⚠️ 存在 {len(overdue_ids)} 张逾期未整改工单，"
                               "请优先督办并核对升级记录。")
        if top_rows:
            top = top_rows[0]
            conclusions.append(f"最高频隐患类别：{top['cls']}"
                               f"（{top['cnt']} 次），建议针对性交底与巡查加密。")
        if total_frames and bad / total_frames > 0.15:
            conclusions.append("不合规帧占比超过 15%，建议评估现场管控措施有效性。")
        if alarms_by_status.get("new", 0):
            conclusions.append(f"仍有 {alarms_by_status['new']} 条告警处于新建状态，"
                               "请确认是否需要转化为处置动作。")
        if not conclusions:
            conclusions.append("周期内各项指标平稳，暂无突出事项。")

        return {
            "start": start or "不限", "end": end or "不限",
            "frames": total_frames, "bad": bad, "warn": int(det["warn"] or 0),
            "ok": int(det["ok"] or 0),
            "top_classes": [{"cls": r["cls"], "count": r["cnt"]} for r in top_rows],
            "alarms_by_status": alarms_by_status,
            "orders_total": total_wo,
            "orders_by_status": wo_by_status,
            "overdue_open_now": len(overdue_ids),
            "per_assignee": per_assignee,
            "conclusions": conclusions,
        }

    # ---------- 渲染 ----------
    def render_pdf(self, stats: dict, out_path: str) -> str:
        """将 gather 结果渲染为中文 PDF 并写入 out_path，返回路径。"""
        from fpdf import FPDF
        from tests.cjk_font import cjk_font_path

        pdf = FPDF()
        pdf.add_page()
        pdf.add_font("cjk", "", cjk_font_path())
        pdf.set_font("cjk", size=16)
        pdf.cell(text="智护工地 · 风险分级周报", align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("cjk", size=11)
        pdf.cell(text=f"统计周期：{stats['start']} ~ {stats['end']}",
                 align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(4)

        def h(title: str) -> None:
            pdf.set_font("cjk", size=13)
            pdf.cell(text=title, new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("cjk", size=11)

        h("一、检测概览")
        pdf.multi_cell(w=0, new_x="LMARGIN", new_y="NEXT", text=(
            f"检测帧总数 {stats['frames']}；不合规 {stats['bad']}、"
            f"警告 {stats['warn']}、合规 {stats['ok']}。"))
        tops = stats["top_classes"] or []
        if tops:
            pdf.multi_cell(w=0, new_x="LMARGIN", new_y="NEXT", text="隐患类别 TOP："
                           + "；".join(f"{t['cls']}×{t['count']}" for t in tops))
        pdf.ln(2)

        h("二、告警概况")
        if stats["alarms_by_status"]:
            pdf.multi_cell(w=0, new_x="LMARGIN", new_y="NEXT", text="；".join(
                f"{k}={v}" for k, v in sorted(stats["alarms_by_status"].items())))
        else:
            pdf.multi_cell(w=0, new_x="LMARGIN", new_y="NEXT", text="周期内无告警记录。")
        pdf.ln(2)

        h("三、工单闭环指标")
        ob = stats["orders_by_status"]
        pdf.multi_cell(w=0, new_x="LMARGIN", new_y="NEXT", text=(
            f"新增工单 {stats['orders_total']} 张：待整改 {ob['open']}、"
            f"待验收 {ob['submitted']}、已销项 {ob['closed']}、"
            f"驳回重改 {ob['rejected']}。当前存量逾期未整改 "
            f"{stats['overdue_open_now']} 张。"))
        pa = stats["per_assignee"]
        if pa:
            pdf.set_font("cjk", size=10)
            for a in pa:
                pdf.multi_cell(w=0, new_x="LMARGIN", new_y="NEXT", text=(
                    f"- {a['name']}：派发 {a['assigned']}｜销项 {a['closed_n']}｜"
                    f"在办 {a['active_n']}｜逾期 {a['overdue_n']}"
                    f"（逾期率 {a['overdue_rate']*100:.0f}%）"))
            pdf.set_font("cjk", size=11)
        pdf.ln(2)

        h("四、结论与建议")
        for line in stats["conclusions"]:
            pdf.multi_cell(w=0, new_x="LMARGIN", new_y="NEXT", text=f"· {line}")

        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        pdf.output(out_path)
        return out_path

    # ---------- 编排 ----------
    def generate(self, start: str, end: str,
                 user_id: str | None = None,
                 out_dir: str | None = None) -> dict:
        """校验权限 → 聚合 → 渲染 → 审计，返回 {ok, data:{file_path, stats}}。"""
        from core.paths import data_path

        if user_id:
            self.permissions.require(user_id, "export")
        stats = self.gather(start, end)
        fname = f"风险周报_{end}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        fpath = os.path.join(out_dir or data_path("exports"), fname)
        self.render_pdf(stats, fpath)
        self.audit.insert(user_id, "report_generate", json.dumps({
            "file": fpath, "start": start, "end": end}, ensure_ascii=False))
        return {"ok": True, "data": {"file_path": fpath, "stats": stats}}
