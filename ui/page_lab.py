"""Agent 测试场（v0.8）：多 Agent 链路的鉴权干跑沙盒。

定位：把"分层兜底 + 多 base 接入"从讲解变成可交互演示——
- 每个 Agent（视觉/规则/融合/处置）可单独试跑，或整链干跑；
- LLM 相关试跑可**选择 provider base**（读 enhance.providers 配置），
  对比不同 base 的润色效果；
- **干跑不入库铁律**：直接调用 Agent，不写 tasks/work_orders/审计——
  演示台账零污染；whitelist/severity 查表等纪律照走，测试场不是后门；
- 仅 admin/safety 可见（导航层隔离 + 页内双保险）。
"""
from __future__ import annotations

import json
import uuid

import streamlit as st
from ui.page_helpers import safe_page

from agents.action_agent import ActionAgent
from agents.base import AgentMessage
from agents.fusion_agent import FusionAgent
from agents.orchestrator import Orchestrator
from agents.rule_agent import RuleAgent
from agents.vision_agent import VisionAgent
from core.config import ConfigLoader
from core.evidence import sanitize_filename
from core.rag_engine import RagEngine
from services.enhance_service import EnhanceEngine

_AGENT_STATUS = {"success": "🟢", "degraded": "🟡", "failed": "🔴",
                 "running": "🔵", "pending": "⚪"}


def _scene_picker() -> str:
    return st.selectbox(
        "场景", ["hot_work", "construction_ppe"],
        format_func=lambda s: "动火作业安全" if s == "hot_work"
        else "施工 PPE / 危险检测",
        key="lab_scene")


def _provider_picker(eng: EnhanceEngine) -> str | None:
    """LLM base 选择：自动（配置链序）或指定 provider（润色试跑用）。"""
    names = [p["name"] for p in eng.providers]
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
    try:
        save_dir = "data/uploads"
        os.makedirs(save_dir, exist_ok=True)
        path = os.path.join(
            save_dir,
            f"lab_{uuid.uuid4().hex[:8]}_{sanitize_filename(uploaded.name, fallback='img')}")
        with open(path, "wb") as f:
            f.write(uploaded.getbuffer())
        return path
    except Exception as exc:  # noqa: BLE001
        st.error(f"图片保存失败：{exc}")
        return None


def _run_agent(agent, agent_name: str, payload: dict,
               task_id: str) -> AgentMessage | None:
    """统一试跑入口：构造 AgentMessage → run → 状态行 + 异常已在基类兜底。"""
    msg = agent.run(AgentMessage(task_id=task_id, agent=agent_name,
                                 status="pending", payload=payload))
    tag = _AGENT_STATUS.get(msg.status, "⚪")
    st.caption(f"{tag} {agent_name} ｜ {msg.status} ｜ {msg.cost_ms}ms"
               + (f" ｜ {msg.error}" if msg.error else ""))
    if msg.status != "success":
        return None
    return msg


def _tab_vision(scene: str) -> None:
    st.caption("视觉 Agent 单独试跑：上传图片 → YOLO 检测 → 白名单映射。零 LLM、零外网。")
    up = st.file_uploader("现场图片", type=["jpg", "jpeg", "png"],
                          key="lab_vision_up")
    if up and st.button("▶ 试跑视觉 Agent", type="primary", key="lab_vision_run"):
        path = _save_lab_image(up)
        if path:
            msg = _run_agent(VisionAgent(scene_id=scene), "vision",
                             {"image_paths": [path]}, f"lab_{uuid.uuid4().hex[:8]}")
            if msg:
                dets = msg.payload.get("detections", [])
                st.session_state["lab_detections"] = dets
                st.success(f"检出 {len(dets)} 项目标（已带入融合试跑的预填输入）")
                if dets:
                    st.dataframe([{
                        "类别": d.get("cls"), "置信度": round(float(d.get("conf", 0)), 3),
                        "描述": d.get("violation_desc"),
                        "bbox": d.get("bbox"),
                    } for d in dets])
                if msg.payload.get("fire_model_limitation"):
                    st.warning(msg.payload["fire_model_limitation"])
                st.caption("违规描述：" + ("；".join(
                    msg.payload.get("violation_descs", [])) or "（无）"))


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
        kb = None
        try:
            kb = ConfigLoader().get_scene(scene).get("kb_collection")
        except Exception:  # noqa: BLE001 场景缺 KB 配置时走默认集合
            kb = None
        rag = RagEngine(collection_name=kb) if kb else RagEngine()
        msg = _run_agent(
            RuleAgent(rag=rag), "rule",
            {"permit_info": {"watcher": watcher, "extinguisher": extinguisher,
                             "fire_blanket": fire_blanket, "approval": approval},
             "violation_descs": [v.strip() for v in vdesc.splitlines() if v.strip()],
             "skip_rag": False},
            f"lab_{uuid.uuid4().hex[:8]}")
        if msg:
            comp = msg.payload.get("compliance", [])
            st.dataframe([{
                "字段": c.get("label"), "取值": c.get("value"),
                "结论": c.get("verdict"),
                "条款": c.get("clause_no") or "—",
                "条款已核实": "✅" if c.get("clause_verified") else "⚠️ 待人工",
            } for c in comp] or [{"字段": "（无输出）", "取值": "", "结论": "",
                                  "条款": "", "条款已核实": ""}])
            tips = msg.payload.get("training_tips", [])
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
        msg = _run_agent(FusionAgent(scene_id=scene), "fusion",
                         {"detections": dets, "compliance": comp},
                         f"lab_{uuid.uuid4().hex[:8]}")
        if msg:
            st.success(f"定级：**{msg.payload.get('risk_level', '—')}**")
            for r in msg.payload.get("reasons", []):
                st.caption(f"- {r}")
            fps = msg.payload.get("filtered_fp", [])
            if fps:
                st.caption(f"误报过滤 {len(fps)} 项：" +
                           "、".join(d.get("cls", "?") for d in fps))


def _tab_action(eng: EnhanceEngine, provider: str | None) -> None:
    st.caption("处置 Agent 试跑：模板话术（确定性，主链路用）+ 可选 LLM 润色对比（按所选 base）。")
    hazard = st.text_input("隐患描述", "动火点附近堆放纸箱，未配监火人",
                           key="lab_hazard")
    level = st.selectbox("风险等级", ["低", "一般", "较大", "重大"],
                         index=2, key="lab_level")
    clause = st.text_input("规范条款（可空）", "第X条：动火作业应清理周边可燃物",
                           key="lab_clause")
    agent = ActionAgent(work_order_dao=None)  # 干跑：无 DAO，polish 天然 no-op
    if st.button("▶ 生成模板话术", key="lab_tpl_run"):
        st.code(agent._template(hazard, clause, level), language=None)
    st.divider()
    if st.button("✨ 用所选 base 润色", key="lab_polish_run",
                 help="仅展示用：真实工单落库后的润色走 LlmEngine 异步链，与主链路无耦合"):
        if not provider:
            st.warning("请在上方选择具体 provider（「自动」不用于润色对比）")
            return
        with st.spinner(f"正在用 {provider} 润色…"):
            out = eng.chat(
                provider,
                "你是工地安全提醒助手。用一线工人听得懂的大白话输出整改提示，"
                "只依据给定信息组织语言，不得编造法规名称或条款编号。",
                f"隐患：{hazard}；规范依据：{clause}；风险等级：{level}。"
                "请输出一段提醒工人的整改提示。",
                num_predict=220)
        if out:
            st.success(f"🟢 {provider} 润色结果（{len(out)} 字）")
            st.write(out)
        else:
            st.error(f"润色失败：{eng.last_error or '未知原因'}")


def _tab_chain(scene: str) -> None:
    st.caption("整链干跑：视觉∥规范 → 融合 → 复核 → 处置，与研判页同一编排器，"
               "**但不落任何表**（无 work_order_dao、不调 save_result、不写审计）。")
    up = st.file_uploader("现场图片（可空：仅作业票链路）", type=["jpg", "jpeg", "png"],
                          key="lab_chain_up")
    c1, c2 = st.columns(2)
    watcher = c1.text_input("监火人", "未指定", key="lab_c_watcher")
    extinguisher = c2.text_input("灭火器", "已配备", key="lab_c_ext")
    if st.button("▶ 整链干跑", type="primary", key="lab_chain_run"):
        images = []
        if up:
            path = _save_lab_image(up)
            if path:
                images = [path]
        permit = {"scene": scene, "watcher": watcher,
                  "extinguisher": extinguisher,
                  "fire_blanket": "已设置", "approval": "已审批"}
        orch = Orchestrator(scene_id=scene)  # 不传 work_order_dao：处置落库天然失效
        with st.spinner("链路执行中…（视觉 3s / 规范 4s 超时预算内）"):
            result = orch.execute(f"lab_{uuid.uuid4().hex[:8]}",
                                  images=images, permit_info=permit)
        st.metric("链路总评", _AGENT_STATUS.get(result.status, "⚪") + " " + result.status)
        payload = result.payload
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

    eng = EnhanceEngine()
    provider = _provider_picker(eng)
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
        _tab_action(eng, provider)
    with t_c:
        _tab_chain(scene)
