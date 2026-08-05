"""页面3：多Agent分步结果面板（page_agents）★核心演示页。

交互：对当前任务运行总控编排器，4 张 Agent 卡片展示状态/输出/耗时。
"""
from __future__ import annotations

import json

import streamlit as st
from ui.page_helpers import safe_page

from agents.orchestrator import Orchestrator
from core.compliance import evaluate
from dao.db import get_conn, init_db
from services.audit_service import AuditService
from services.task_service import TaskService
from ui.components import compliance_banner

_STATUS_COLOR = {
    "success": "🟢", "degraded": "🟡", "failed": "🔴",
    "running": "🔵", "pending": "⚪",
}
_AGENT_TITLE = {
    "vision": "感知视觉 Agent（巡检员）",
    "rule": "安全规范 Agent（资料员）",
    "fusion": "风险融合 Agent（安全主管）",
    "review": "复核 Agent（质检员）",
    "action": "闭环处置 Agent（督办员）",
}


@safe_page("多Agent研判")
def render_agents() -> None:
    st.title("🤖 多Agent 分步研判")
    task_id = st.session_state.get("current_task_id")
    if not task_id:
        st.warning("请先在上传页创建任务")
        return

    permit_info = st.session_state.get("permit_info", {})
    images = []
    if st.session_state.get("uploaded_path"):
        images = [st.session_state["uploaded_path"]]

    conn = get_conn()
    init_db(conn)
    ts = TaskService(conn)

    if st.button("▶ 运行多Agent研判", type="primary") or st.session_state.get("_ran"):
        scene_id = st.session_state.get("scene", "hot_work")
        orch = Orchestrator(progress_cb=ts.update_progress, scene_id=scene_id)
        result = orch.execute(task_id, images=images, permit_info=permit_info)
        st.session_state["_ran"] = True
        st.session_state["_result"] = result.to_dict()
        # 持久化研判结果到数据库（幂等，已有则跳过）
        ts.save_result(task_id, result.payload)
        AuditService(conn).append(st.session_state.get("user_id"), "execute", {"task_id": task_id})

    result = st.session_state.get("_result")
    if not result:
        st.info("点击上方按钮运行研判")
        return

    # 统一三级合规横幅（复用 realtime 的同款组件，保持双模式一致）
    payload = result.get("payload", {}) if isinstance(result, dict) else {}
    vision_payload = payload.get("vision", {})
    vp = vision_payload.get("payload", {}) if isinstance(vision_payload, dict) else {}
    dets = vp.get("detections", []) if isinstance(vp, dict) else []
    comp = evaluate(dets)
    risk_level = payload.get("risk_level") or result.get("risk_level")
    compliance_banner(comp, risk_level=risk_level,
                      subtitle=f"检出目标 {len(dets)} 项")

    # 顶部进度条
    prog = ts.get_progress(task_id)
    cols = st.columns(len(_AGENT_TITLE))
    for i, (agent, title) in enumerate(_AGENT_TITLE.items()):
        info = prog.get(agent, {})
        cols[i].metric(title.split("（")[0], info.get("status", "—"), f"{info.get('cost_ms',0)}ms")

    with st.expander("证据链 / Agent 运行轨迹"):
        runs = ts.list_agent_runs(task_id)
        if not runs:
            st.caption("本次运行结果保存后自动生成，用于追溯每个 Agent 的耗时与输出。")
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

    # 4 张 Agent 卡片（orchestrator 返回的是 AgentMessage dict，数据在 payload 里）
    result_payload = result.get("payload", {})
    for agent, title in _AGENT_TITLE.items():
        node = result_payload.get(agent, {})
        status = node.get("status", "—")
        color = _STATUS_COLOR.get(status, "⚪")
        with st.container(border=True):
            st.subheader(f"{color} {title}")
            payload = node.get("payload", {})
            if agent == "vision":
                dets = payload.get("detections", [])
                st.write(f"检出目标：{len(dets)} 项")
                if not dets and payload.get("fire_model_limitation"):
                    st.warning(payload["fire_model_limitation"])
                for d in dets:
                    st.caption(f"- {d.get('cls')} (conf {d.get('conf')})")
            elif agent == "rule":
                if status != "success":
                    err = payload.get("error") or node.get("error") or "未知异常"
                    st.error(f"运行异常：{err}")
                else:
                    comp = payload.get("compliance", [])
                    if not comp:
                        st.info("作业票字段全部合规")
                    for c in comp:
                        verdict = c.get("verdict", "合规")
                        label = c.get("label", "")
                        clause = (c.get("clause_ref") or c.get("clause_no")
                                  or c.get("clause", ""))
                        clause_text = c.get("clause_text", "")
                        icon = "✅" if verdict == "合规" else "❌"
                        clause_display = f"（第{clause}条）" if clause else ""
                        if clause_text:
                            clause_display += f"：{clause_text[:60]}"
                        st.caption(
                            f"{icon} {label}：{verdict}{clause_display}"
                        )
                tips = payload.get("training_tips") or []
                if tips:
                    st.markdown("**培训要点**")
                    for tip in tips:
                        st.caption(f"- {tip}")
            elif agent == "fusion":
                st.write(f"风险等级：**{payload.get('risk_level','—')}**")
                for r in payload.get("reasons", []):
                    st.caption(f"- {r}")
            elif agent == "review":
                needs_review = payload.get("needs_review", False)
                if needs_review:
                    st.warning("需要人工复核")
                    for r in payload.get("review_reasons", []):
                        st.caption(f"- {r}")
                else:
                    st.success("无需人工复核")
            elif agent == "action":
                wo = payload.get("work_order", {})
                if wo:
                    st.write(f"隐患：{wo.get('hazard_desc','')}")
                    st.write(f"整改：{wo.get('requirement','')[:60]}…")
                    st.success(f"工人提示：{payload.get('worker_notice','')[:80]}…")

    if st.button("查看工单 / 改判 / 导出 →"):
        st.session_state["report_result"] = result
        st.session_state["_nav_page"] = "report"
        st.rerun()
