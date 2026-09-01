"""Agent 测试场（v0.8）：研判链路的鉴权干跑沙盒。

定位：把"分层兜底 + 多 base 接入"从讲解变成可交互演示——
- 每个 Agent（视觉/规范/融合/处置）可单独试跑，或整链干跑；
- LLM 相关试跑可**选择 provider base**（读 enhance.providers 配置），
  对比不同 base 的润色效果；
- **干跑不入库铁律**：试跑经 services.lab_service 直调 agents，
  不写 tasks/work_orders/审计——演示台账零污染；白名单/severity
  纪律照走，测试场不是后门；
- 仅 admin/safety 可见（导航层隔离 + 页内双保险）。
Phase 0：所有试跑逻辑在 services.lab_service，本页纯渲染。
"""
from __future__ import annotations

import json

import streamlit as st
from ui.page_helpers import safe_page

# 白名单（情况1·纯函数）：仅做测试场图片文件名消毒（无业务计算）。
from core.evidence import sanitize_filename
from services import lab_service

_AGENT_STATUS = {"success": "🟢", "degraded": "🟡", "failed": "🔴",
                 "running": "🔵", "pending": "⚪"}


def _scene_picker() -> str:
    return st.selectbox(
        "场景", ["hot_work", "construction_ppe"],
        format_func=lambda s: "动火作业安全" if s == "hot_work"
        else "施工 PPE / 危险检测",
        key="lab_scene")


def _provider_picker() -> str | None:
    """LLM base 选择：自动（配置链序）或指定 provider（润色试跑用）。"""
    names = lab_service.provider_names()
    if not names:
        st.caption("enhance 未配置任何 provider，润色试跑不可用（模板仍可用）")
        return None
    pick = st.selectbox("LLM Base（润色试跑通道）",
                        ["自动（配置链序）"] + names, key="lab_provider")
    if pick == "自动（配置链序）":
        return None
    return pick


def _save_lab_image(uploaded) -> str | None:
    """测试场图片落盘（data/uploads/lab_*，.gitignore 已覆盖，不入库表）。"""
    import os
    from core.paths import data_path
    try:
        save_dir = data_path("uploads")
        os.makedirs(save_dir, exist_ok=True)
        path = os.path.join(
            save_dir,
            f"lab_{lab_service.lab_task_id()}_{sanitize_filename(uploaded.name, fallback='img')}")
        with open(path, "wb") as f:
            f.write(uploaded.getbuffer())
        return path
    except Exception as exc:  # noqa: BLE001
        st.error(f"图片保存失败：{exc}")
        return None


def _show_agent_result(res: dict, name: str) -> None:
    tag = _AGENT_STATUS.get(res.get("status", "pending"), "⚪")
    st.caption(f"{tag} {name} ｜ {res.get('status')} ｜ {res.get('cost_ms')}ms"
               + (f" ｜ {res.get('error')}" if res.get("error") else ""))


def _tab_vision(scene: str) -> None:
    st.caption("视觉 Agent 单独试跑：上传图片 → YOLO 检测 → 白名单映射。零 LLM、零外网。")
    up = st.file_uploader("现场图片", type=["jpg", "jpeg", "png"],
                          key="lab_vision_up")
    if up and st.button("▶ 试跑视觉 Agent", type="primary", key="lab_vision_run"):
        path = _save_lab_image(up)
        if path:
            res = lab_service.run_vision(scene, path)
            _show_agent_result(res, "vision")
            if res.get("status") == "success":
                dets = res["payload"].get("detections", [])
                st.session_state["lab_detections"] = dets
                st.success(f"检出 {len(dets)} 项目标（已带入融合试跑的预填输入）")
                if dets:
                    st.dataframe([{
                        "类别": d.get("cls"), "置信度": round(float(d.get("conf", 0)), 3),
                        "描述": d.get("violation_desc"),
                        "bbox": d.get("bbox"),
                    } for d in dets])
                if res["payload"].get("fire_model_limitation"):
                    st.warning(res["payload"]["fire_model_limitation"])
                st.caption("违规描述：" + ("；".join(
                    res["payload"].get("violation_descs", [])) or "（无）"))


def _tab_rule(scene: str) -> None:
    st.caption("规范 Agent 单独试跑：作业票字段 + 违规描述 → RAG 检索条款 → 合规判定。")
    c1, c2 = st.columns(2)
    watcher = c1.text_input("监火人", "已指定", key="lab_watcher")
    extinguisher = c2.text_input("灭火器", "已配备", key="lab_ext")
    c3, c4 = st.columns(2)
    fire_blanket = c3.text_input("防火毯", "已设置", key="lab_blanket")
    approval = c4.text_input("作业审批", "已审批", key="lab_approval")
    vdesc = st.text_area("违规描述（每行一条）", "动火点附近堆放纸箱",
                         key="lab_vdesc", height=80)
    if st.button("▶ 试跑规范 Agent", type="primary", key="lab_rule_run"):
        res = lab_service.run_rule(
            scene,
            {"watcher": watcher, "extinguisher": extinguisher,
             "fire_blanket": fire_blanket, "approval": approval},
            [v.strip() for v in vdesc.splitlines() if v.strip()])
        _show_agent_result(res, "rule")
        if res.get("status") == "success":
            comp = res["payload"].get("compliance", [])
            st.dataframe([{
                "字段": c.get("label"), "取值": c.get("value"),
                "结论": c.get("verdict"),
                "条款": c.get("clause_no") or "—",
                "条款已核实": "✅" if c.get("clause_verified") else "⚠️ 待人工",
            } for c in comp] or [{"字段": "（无输出）", "取值": "", "结论": "",
                                  "条款": "", "条款已核实": ""}])
            tips = res["payload"].get("training_tips", [])
            if tips:
                st.markdown("**培训要点**")
                for t in tips:
                    st.caption(f"- {t}")


def _tab_fusion(scene: str) -> None:
    st.caption("融合 Agent 单独试跑：检测组合 + 合规结论 → 风险矩阵定级 + 误报过滤。纯规则查表。")
    prefill = st.session_state.get("lab_detections") or [
        {"cls": "spark", "conf": 0.9, "bbox": [10, 10, 50, 50]}]
    det_json = st.text_area("检测输入 JSON（视觉试跑后自动预填）",
                            json.dumps(prefill, ensure_ascii=False),
                            key="lab_det_json", height=110)
    comp_json = st.text_area("合规结论 JSON（可选）", "[]", key="lab_comp_json",
                             height=60)
    if st.button("▶ 试跑融合 Agent", type="primary", key="lab_fusion_run"):
        try:
            dets = json.loads(det_json or "[]")
            comp = json.loads(comp_json or "[]")
        except ValueError as e:
            st.error(f"JSON 解析失败：{e}")
            return
        res = lab_service.run_fusion(scene, dets, comp)
        _show_agent_result(res, "fusion")
        if res.get("status") == "success":
            st.success(f"定级：**{res['payload'].get('risk_level', '—')}**")
            for r in res["payload"].get("reasons", []):
                st.caption(f"- {r}")
            fps = res["payload"].get("filtered_fp", [])
            if fps:
                st.caption(f"误报过滤 {len(fps)} 项：" +
                           "、".join(d.get("cls", "?") for d in fps))


def _tab_action(provider: str | None) -> None:
    st.caption("处置 Agent 试跑：模板话术（确定性，主链路用）+ 可选 LLM 润色对比（按所选 base）。")
    hazard = st.text_input("隐患描述", "动火点附近堆放纸箱，未配监火人",
                           key="lab_hazard")
    level = st.selectbox("风险等级", ["低", "一般", "较大", "重大"],
                         index=2, key="lab_level")
    clause = st.text_input("规范条款（可空）", "第X条：动火作业应清理周边可燃物",
                           key="lab_clause")
    if st.button("▶ 生成模板话术", key="lab_tpl_run"):
        st.code(lab_service.template_notice(hazard, clause, level), language=None)
    st.divider()
    if st.button("✨ 用所选 base 润色", key="lab_polish_run",
                 help="仅展示用：真实工单落库后的润色走 LlmEngine 异步链，与主链路无耦合"):
        if not provider:
            st.warning("请在上方选择具体 provider（「自动」不用于润色对比）")
            return
        with st.spinner(f"正在用 {provider} 润色…"):
            out, err = lab_service.polish_with(provider, hazard, clause, level)
        if out:
            st.success(f"🟢 {provider} 润色结果（{len(out)} 字）")
            st.write(out)
        else:
            st.error(f"润色失败：{err or '未知原因'}")


def _tab_chain(scene: str) -> None:
    st.caption("整链干跑：视觉∥规范 → 融合 → 复核 → 处置，与研判页同一编排器，"
               "**但不落任何表**（无 work_order_dao、不调 save_result、不写审计）。")
    up = st.file_uploader("现场图片（可空：仅作业票链路）", type=["jpg", "jpeg", "png"],
                          key="lab_chain_up")
    c1, c2 = st.columns(2)
    watcher = c1.text_input("监火人", "未指定", key="lab_c_watcher")
    extinguisher = c2.text_input("灭火器", "已配备", key="lab_c_ext")
    if st.button("▶ 整链干跑", type="primary", key="lab_chain_run"):
        image_path = _save_lab_image(up) if up else None
        permit = {"scene": scene, "watcher": watcher,
                  "extinguisher": extinguisher,
                  "fire_blanket": "已设置", "approval": "已审批"}
        with st.spinner("链路执行中…（视觉 3s / 规范 4s 超时预算内）"):
            result = lab_service.run_chain(scene, image_path, permit)
        st.metric("链路总评", _AGENT_STATUS.get(result.get("status", "pending"), "⚪")
                  + " " + str(result.get("status")))
        payload = result.get("payload", {})
        for name in ("vision", "rule", "fusion", "review", "action"):
            node = payload.get(name, {})
            st.caption(f"{_AGENT_STATUS.get(node.get('status', 'pending'), '⚪')} "
                       f"{name} ｜ {node.get('status', '—')}")
        st.caption(f"风险定级：**{payload.get('risk_level') or '—'}**")
        for r in payload.get("reasons") or []:
            st.caption(f"- {r}")
        wo = payload.get("work_order") or {}
        if wo:
            with st.expander("处置产出（仅展示，未入库）"):
                st.code(wo.get("worker_notice") or "", language=None)


@safe_page("Agent 测试场")
def render_lab() -> None:
    st.title("🧪 Agent 测试场")
    if st.session_state.get("role") not in ("admin", "safety"):
        st.error("测试场仅对安全员/管理员开放")
        return

    provider = _provider_picker()
    st.caption("干跑沙盒：所有试跑**不写任何数据库表**；白名单/severity 纪律照走。"
               "LLM 仅用于预填/润色类展示，判定路径永远纯规则。")

    t_v, t_r, t_f, t_a, t_c = st.tabs(
        ["👁 视觉", "📖 规范", "⚖️ 融合", "📨 处置润色", "🔗 整链干跑"])
    scene = _scene_picker()
    with t_v:
        _tab_vision(scene)
    with t_r:
        _tab_rule(scene)
    with t_f:
        _tab_fusion(scene)
    with t_a:
        _tab_action(provider)
    with t_c:
        _tab_chain(scene)
