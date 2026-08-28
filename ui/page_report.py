"""页面4：工单预览 / 改判 / 导出 / 历史记录（page_report）。

Phase 0：派发/改判/导出/历史/检测明细全部经 services.order_service /
services.lookup_service / services.task_entry，本页零 get_conn/DAO/core；
三级合规研判经 task_entry.evaluate_compliance（业务计算在服务层），
横幅渲染（ui.components.compliance_banner）为纯视图转换。
"""
from __future__ import annotations

import json

import streamlit as st
from ui.page_helpers import safe_page

from services import lookup_service, order_service, task_entry
from services.permission_service import AuthorizationError as ServicePermissionError
from ui.components import compliance_banner
from ui.correction_workbench import render_target_corrections

RISK_EMOJI = {"重大": "🔴", "较大": "🟠", "一般": "🟡", "低": "🟢"}
_WO_STATUS_TAG = {
    "open": "🔨 待整改",
    "rejected": "↩️ 已驳回",
    "submitted": "⏳ 待验收",
    "closed": "✅ 已销项",
}
_SOURCE_LABEL = {
    "camera": "📷 实时摄像头",
    "upload": "📤 图片上传",
    "text": "📝 文字上报",
}


def _render_dispatch_panel(task_id: str, risk_level: str | None) -> None:
    """派发与整改闭环面板：状态一览 + 责任人指派（规则预选）。"""
    scene = st.session_state.get("scene", "hot_work")
    panel = order_service.dispatch_panel(task_id, scene_id=scene)

    st.subheader("📮 派发与整改闭环")
    if panel is None:
        st.caption("本任务尚未生成工单")
        return
    wo = panel["order"]

    c1, c2, c3 = st.columns(3)
    c1.metric("闭环状态", _WO_STATUS_TAG.get(wo["status"], wo["status"]))
    c2.metric("责任人", panel["assignee_name"] or "未派发")
    c3.metric("截止", (wo["deadline"] or "—")[:19])
    if wo["status"] == "rejected":
        st.error(f"❌ 验收驳回原因:{wo['review_reason'] or '未填写'}——请改派或通知责任人重新提交。")
    elif wo["status"] == "closed":
        st.success(f"已销项 ✅ 验收人:{wo['approved_by'] or '—'}")

    # 写操作仅安全员/管理员可用；responsible 在"我的整改单"页提交材料（读写隔离）
    role = st.session_state.get("role")
    uid = st.session_state.get("user_id")
    if role not in ("safety", "admin"):
        st.caption(f"当前角色为 {role or '访客'}：可查看流转状态，派发需安全员/管理员操作。")
        return
    if wo["status"] in ("submitted", "closed"):
        st.caption("工单在验收流程中（submitted/closed），不可改派。")
        return

    names = panel["responsible_names"]
    if not names:
        st.warning("系统中暂无 responsible 责任人账号"
                   "（可在管理端「用户管理」创建，role=responsible）。")
        return

    suggestion = panel["suggestion"]
    idx = names.index(suggestion) if suggestion in names else 0
    tag_text = "改派" if panel["assignee_name"] else "派发"
    ca, cb, cc = st.columns([2, 1.2, 1])
    chosen = ca.selectbox(
        "整改责任人", names, index=idx,
        help=f"默认按 dispatch.rules 对场景「{scene}」的命中建议{suggestion or ''}")
    hours = cb.number_input(
        "整改时限（小时）", min_value=0.5, max_value=720.0,
        value=float(panel["default_hours"] or 24), step=1.0)
    if cc.button(f"{tag_text} 工单", type="primary",
                 key=f"dispatch_{task_id}", use_container_width=True):
        try:
            ok, msg = order_service.dispatch_order(
                task_id, uid, chosen, float(hours), scene_id=scene)
        except ServicePermissionError as e:
            st.error(f"权限不足：{e}")
            return
        except ValueError as e:
            st.error(str(e))
            return
        if ok:
            st.success(f"已{tag_text}给 {chosen}，截止 {hours:.0f} 小时后")
            st.rerun()
        else:
            st.error(msg)


def _show_work_order(payload: dict, task_id: str) -> None:
    """渲染单个工单详情卡片。"""
    wo = payload.get("work_order") or {}
    # 统一三级合规横幅（基于视觉检测结果，与实时态一致）
    vision_payload = payload.get("vision", {})
    vp = vision_payload.get("payload", {}) if isinstance(vision_payload, dict) else {}
    dets = vp.get("detections", []) if isinstance(vp, dict) else []
    comp = task_entry.evaluate_compliance(dets)
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

    st.divider()
    _render_dispatch_panel(task_id, payload.get("risk_level"))

    with st.expander("Agent 证据链"):
        runs = task_entry.agent_runs(task_id)
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
            uid = st.session_state.get("user_id")
            ok, msg = order_service.submit_override(
                task_id, uid, new_level, reason,
                image_path=st.session_state.get("uploaded_path"),
                detections=dets)
            st.success(msg) if ok else st.error(msg)

    st.divider()
    with st.expander("逐目标纠偏（可生成训练样本）"):
        if not dets:
            st.caption("当前任务没有视觉检测目标")
        else:
            corrections = render_target_corrections(
                st.session_state.get("uploaded_path"),
                dets, [], f"report_{task_id}")
            if st.button("保存逐目标纠偏", key=f"save_fix_{task_id}"):
                order_service.save_detection_fix(
                    task_id, st.session_state.get("user_id"),
                    payload.get("risk_level", "一般"),
                    st.session_state.get("uploaded_path"),
                    dets, corrections)
                st.success("逐目标纠偏已保存为待审核反馈样本")

    st.divider()
    st.subheader("导出台账")
    if st.button("导出 Excel 台账", key="btn_export"):
        ok, msg = order_service.export_excel(
            task_id, st.session_state.get("user_id"))
        if ok:
            st.success(f"已导出：{msg}")
        else:
            st.error(msg)


def _render_history_list() -> None:
    """历史研判记录列表。"""
    rows = lookup_service.history_orders()
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
            source_label = _SOURCE_LABEL.get(row["source"], "📤 图片上传")
            status_tag = _WO_STATUS_TAG.get(row["status"], "—")
            st.write(f"**任务编号**：{row['task_id']}")
            st.write(f"**输入来源**：{source_label}　|　**闭环状态**：{status_tag}")
            if row["deadline"]:
                st.write(f"**整改截止**：{row['deadline'][:19]}"
                         f"　|　**责任人ID**：{row['assignee_id'] or '未派发'}")
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
                detail = lookup_service.task_detection_detail(row["task_id"])
                detections = detail["detections"]
                comps = detail["compliances"]
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
