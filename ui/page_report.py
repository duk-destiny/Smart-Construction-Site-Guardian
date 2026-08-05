"""页面4：工单预览 / 改判 / 导出 / 历史记录（page_report）。"""
from __future__ import annotations

import json

import streamlit as st
from ui.page_helpers import safe_page

from dao.db import get_conn, init_db
from dao.models import WorkOrderDAO, RiskDAO
from core.compliance import evaluate
from core.yolo_engine import WHITELIST
from services.audit_service import AuditService
from services.export_service import ExportService
from services.task_service import TaskService
from ui.components import compliance_banner
from ui.correction_workbench import render_target_corrections

RISK_EMOJI = {"重大": "🔴", "较大": "🟠", "一般": "🟡", "低": "🟢"}


def _show_work_order(payload: dict, task_id: str) -> None:
    """渲染单个工单详情卡片。"""
    wo = payload.get("work_order") or {}
    # 统一三级合规横幅（基于视觉检测结果，与实时态一致）
    vision_payload = payload.get("vision", {})
    vp = vision_payload.get("payload", {}) if isinstance(vision_payload, dict) else {}
    dets = vp.get("detections", []) if isinstance(vp, dict) else []
    comp = evaluate(dets)
    compliance_banner(comp, risk_level=payload.get("risk_level"),
                      subtitle=f"检出目标 {len(dets)} 项")

    st.subheader("整改工单")
    st.caption(f"任务编号：{task_id}")
    st.write(f"**隐患描述**：{wo.get('hazard_desc','')}")
    st.write(f"**违反规范**：{wo.get('clause','')}")
    st.write(f"**整改要求**：{wo.get('requirement','')}")
    st.write(f"**风险等级**：{payload.get('risk_level','—')}")
    if wo.get("review_required"):
        st.warning("该工单需要人工复核：" + "；".join(wo.get("review_reasons") or []))
    st.info(f"💬 工人白话提示：{payload.get('worker_notice','')}")

    with st.expander("Agent 证据链"):
        conn = get_conn()
        init_db(conn)
        runs = TaskService(conn).list_agent_runs(task_id)
        if not runs:
            st.caption("暂无 Agent 运行记录")
        for run in runs:
            st.caption(
                f"{run['agent']} ｜ {run['status']} ｜ {run['cost_ms']}ms ｜ {run['created_at']}"
            )
            if run["input_json"]:
                try:
                    st.json({"输入": json.loads(run["input_json"])})
                except ValueError:
                    pass
            if run["output_json"]:
                try:
                    st.json({"输出": json.loads(run["output_json"])})
                except ValueError:
                    st.caption(run["output_json"][:200])

    st.divider()
    st.subheader("人工改判")
    new_level = st.selectbox("改判风险等级", ["低", "一般", "较大", "重大"], key="override_level")
    reason = st.text_input("改判原因（必填）", key="override_reason")
    if st.button("提交改判", key="btn_override"):
        if not reason:
            st.error("请填写改判原因")
        else:
            conn = get_conn()
            init_db(conn)
            ts = TaskService(conn)
            ok = ts.manual_override(
                task_id, new_level, reason,
                user_id=st.session_state.get("user_id"))
            risk = RiskDAO(conn).get_by_task(task_id) if ok else None
            ts.save_feedback_sample(
                task_id=task_id,
                user_id=st.session_state.get("user_id"),
                corrected_level=new_level,
                reason=reason,
                auto_level=risk["risk_level"] if risk else None,
                source_json={
                    "reasons_json": risk["reasons_json"] if risk else None,
                    "filtered_fp_json": risk["filtered_fp_json"] if risk else None,
                },
                image_path=st.session_state.get("uploaded_path"),
                detections=dets,
                corrected_labels=[{
                    "risk_level": new_level,
                    "reason": reason,
                }],
            )
            AuditService(conn).append(st.session_state.get("user_id"), "override",
                                     {"task_id": task_id, "level": new_level, "reason": reason})
            st.success("改判已记录" if ok else "未找到该任务风险记录")

    st.divider()
    with st.expander("逐目标纠偏（可生成训练样本）"):
        if not dets:
            st.caption("当前任务没有视觉检测目标")
        else:
            corrections = render_target_corrections(
                st.session_state.get("uploaded_path"),
                dets, [], f"report_{task_id}")
            if st.button("保存逐目标纠偏", key=f"save_fix_{task_id}"):
                conn = get_conn()
                init_db(conn)
                ts = TaskService(conn)
                ts.save_feedback_sample(
                    task_id=task_id,
                    user_id=st.session_state.get("user_id"),
                    corrected_level=payload.get("risk_level", "一般"),
                    reason="逐目标纠偏",
                    auto_level=payload.get("risk_level", "一般"),
                    feedback_type="detection_fix",
                    image_path=st.session_state.get("uploaded_path"),
                    detections=dets,
                    corrected_labels=corrections,
                )
                AuditService(conn).append(
                    st.session_state.get("user_id"), "detection_fix",
                    {"task_id": task_id, "items": len(corrections)})
                st.success("逐目标纠偏已保存为待审核反馈样本")

    st.divider()
    st.subheader("导出台账")
    if st.button("导出 Excel 台账", key="btn_export"):
        conn = get_conn()
        init_db(conn)
        r = ExportService(conn).export_excel(
            task_id=task_id,
            user_id=st.session_state.get("user_id"))
        if r["ok"]:
            st.success(f"已导出：{r['data']['file_path']}")
        else:
            st.error("导出失败")


def _render_history_list() -> None:
    """历史研判记录列表。"""
    conn = get_conn()
    init_db(conn)
    wo_dao = WorkOrderDAO(conn)
    rows = wo_dao.list_all_with_risk()
    if not rows:
        st.info("暂无历史研判记录")
        return

    risk_filter = st.selectbox("按风险等级筛选", ["全部", "重大", "较大", "一般", "低"], key="risk_filter")

    filtered = []
    for row in rows:
        level = row["override_level"] or row["auto_level"] or ""
        if risk_filter != "全部" and level != risk_filter:
            continue
        filtered.append(row)

    if not filtered:
        st.info(f"无「{risk_filter}」等级的研判记录")
        return

    st.caption(f"共 {len(filtered)} 条记录，点击查看详情")

    for row in filtered:
        level = row["override_level"] or row["auto_level"] or "—"
        emoji = RISK_EMOJI.get(level, "⚪")
        desc = (row["hazard_desc"] or "")[:40]
        if len(row["hazard_desc"] or "") > 40:
            desc += "…"
        ts = row["created_at"]
        override_tag = " ✎已改判" if row["override_level"] else ""
        label = f"{emoji} [{level}]{override_tag}  {ts}  —  {desc}"

        with st.expander(label):
            st.write(f"**任务编号**：{row['task_id']}")
            st.write(f"**时间**：{ts}")
            st.write(f"**隐患描述**：{row['hazard_desc']}")
            st.write(f"**违反规范**：{row['clause']}")
            st.write(f"**整改要求**：{row['requirement']}")
            st.write(f"**风险等级**：{level}")
            if row["override_level"]:
                st.warning(f"人工改判 → {row['override_level']}（原因：{row['override_reason']}）")
            st.info(f"💬 工人白话提示：{row['worker_notice']}")

            # 查看检测详情
            if st.button("查看检测数据", key=f"detail_{row['task_id']}"):
                detections = conn.execute(
                    "SELECT * FROM detections WHERE task_id=?",
                    (row["task_id"],)).fetchall()
                comps = conn.execute(
                    "SELECT * FROM compliances WHERE task_id=?",
                    (row["task_id"],)).fetchall()
                if detections:
                    st.caption(f"视觉检测结果（{len(detections)} 条）")
                    for d in detections:
                        st.write(f"- **{d['violation_desc'] or d['cls']}** 置信度 {d['conf']:.2f}")
                if comps:
                    st.caption(f"规范合规结果（{len(comps)} 条）")
                    for c in comps:
                        st.write(f"- {c['verdict']} | {c['clause_text']}")


@safe_page("工单/改判/导出")
def render_report() -> None:
    st.title("📋 整改工单 / 历史记录")

    result = st.session_state.get("report_result") or st.session_state.get("_result")
    task_id = st.session_state.get("current_task_id")

    # 当前工单详情（如果刚从研判页跳转过来）
    if result and task_id:
        payload = result.get("payload", {}) if isinstance(result, dict) and "payload" in result else result
        _show_work_order(payload, task_id)
        st.divider()
        st.subheader("📚 历史研判记录")
    else:
        st.subheader("📚 历史研判记录")

    _render_history_list()
