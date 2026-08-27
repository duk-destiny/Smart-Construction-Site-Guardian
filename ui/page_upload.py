"""页面2：统一上报（page_upload）——v0.4 按方案文档 4.4 原地升级为三个 Tab。

Tab① 影像研判：原有 图片/视频 + 作业票 → create_task(source=upload) → 重链路；
Tab② 文字/语音上报：自由文本线索直接建单（source=text，跳过视觉链路，
    风险按 compliance.severity 查表），语音为可选转写调用——
    未配置 `asr.*` 时入口**完全不渲染**（静默，用户约定）；
Tab③ 工单速查：输入工单号直达详情（跳报告页历史记录，只读）。
"""
from __future__ import annotations

import streamlit as st
from ui.page_helpers import safe_page

from core.asr_engine import AsrEngine
from core.compliance import SEVERITY
from core.yolo_engine import WHITELIST_CN
from dao.db import get_conn, init_db
from services.task_service import TaskService


def _tab1_media(svc: TaskService) -> None:
    uploaded = st.file_uploader("现场图片/视频", type=["jpg", "jpeg", "png", "mp4"])
    if uploaded:
        (st.image(uploaded, caption="预览", width=320)
         if uploaded.type.startswith("image") else st.video(uploaded))

    scene = st.selectbox("作业类型 / 危险检测场景",
                         ["hot_work", "construction_ppe"],
                         format_func=lambda s: "动火作业安全" if s == "hot_work"
                         else "施工 PPE / 危险检测", index=0)
    st.session_state["scene"] = scene
    st.caption("动火作业安全：火情/火花/烟雾 + 动火规范；施工 PPE：安全帽/反光衣检测")

    is_hot = scene == "hot_work"
    st.subheader("作业票信息")
    with st.form("permit_form"):
        if is_hot:
            fire_level = st.selectbox("动火级别", ["一级", "二级"])
            watcher = st.text_input("监火人")
        else:
            fire_level = "—"
            watcher = st.text_input("安全员", "已指定")
        valid_until = st.text_input("有效期限")
        area = st.text_input("作业区域")
        extinguisher = st.text_input("灭火器配置" if is_hot else "防护装备确认",
                                     "已配备" if is_hot else "已确认")
        fire_blanket = st.text_input("防火毯" if is_hot else "现场清理确认",
                                     "已设置" if is_hot else "已完成")
        approval = st.text_input("作业审批", "已审批")
        submitted = st.form_submit_button("开始智能研判")

    if submitted:
        permit_info = {
            "scene": scene, "fire_level": fire_level, "watcher": watcher,
            "valid_until": valid_until, "area": area,
            "extinguisher": extinguisher, "fire_blanket": fire_blanket,
            "approval": approval,
        }
        tid = svc.create_task(st.session_state.get("user_id", "u_demo"), [],
                              permit_info)
        if uploaded:
            import os
            save_dir = "data/uploads"
            os.makedirs(save_dir, exist_ok=True)
            path = os.path.join(save_dir, f"{tid}_{uploaded.name}")
            with open(path, "wb") as f:
                f.write(uploaded.getbuffer())
            st.session_state["uploaded_path"] = path
        _goto_task(tid, permit_info, next_page="agents")


def _hazard_options() -> list[str]:
    """隐患键白名单下拉项：仅保留 critical/warning，正向 safe 排除；高危置顶。"""
    items = [k for k, v in SEVERITY.items()
             if k != "none" and v in ("critical", "warning")]
    items.sort(key=lambda k: (0 if SEVERITY[k] == "critical" else 1,
                              WHITELIST_CN.get(k, k)))
    return items


def _option_label(key: str) -> str:
    sev_tag = {"critical": "🔴 高危", "warning": "🟡 关注"}[SEVERITY[key]]
    return f"{sev_tag}｜{WHITELIST_CN.get(key, key)}（{key}）"


def _tab2_text_voice(svc: TaskService) -> None:
    st.caption("适合摄像头拍不到的隐患（无证上岗、无交底、通道占用等）。"
               "提交后**跳过视觉研判**，风险按规则查表定级，直接进入派发闭环。")

    scene_t2 = st.selectbox("归属场景", ["hot_work", "construction_ppe"],
                            format_func=lambda s: "动火作业安全" if s == "hot_work"
                            else "施工 PPE / 危险检测",
                            index=0, key="t2_scene")
    options = _hazard_options()

    # —— 语音入口：未配置 asr.* 时整块不渲染（静默，用户约定）——
    asr = AsrEngine()
    if asr.available():
        audio = st.audio_input("🎤 或先说一段话，转写后自动填入下方文本框")
        ca, cb = st.columns([1, 3])
        if ca.button("🎤 转写", key="btn_asr"):
            if audio is None:
                st.warning("请先录音")
            else:
                text = asr.transcribe(audio.getvalue(),
                                      getattr(audio, "name", None) or "record.wav")
                if text:
                    st.session_state["t2_desc"] = text
                    st.success("转写完成，已填入描述（可修改后提交）")
                    st.rerun()
                else:
                    st.warning(f"转写暂不可用：{asr.last_error or '未知原因'}，"
                               "可直接手填。")

    desc = st.text_area(
        "隐患描述", height=90, key="t2_desc",
        placeholder="例：3号楼西侧电焊机旁堆着纸箱没人清理，也没有监火人")
    hkey = st.selectbox("隐患类别（可后续在派发面板改派时调整）", options,
                        format_func=_option_label, key="t2_hazard")
    location = st.text_input("位置（可选）", key="t2_area",
                             placeholder="如：3号楼西侧 / 地库B区")

    if st.button("📝 创建文字隐患单", type="primary", use_container_width=True):
        try:
            tid = svc.create_text_hazard(
                st.session_state.get("user_id", "u_demo"), desc, hkey,
                scene_id=scene_t2, location=location)
        except ValueError as e:
            st.error(str(e))
        else:
            from core.config import ConfigLoader  # noqa: F401 占位：便于后续扩展场景文案
            risk_row = svc.risks.get_by_task(tid)
            level = risk_row["risk_level"] if risk_row else "一般"
            wo = svc.work_orders.get_by_task(tid)
            payload = {
                "risk_level": level,
                "vision": {"payload": {"detections": []}},
                "work_order": {
                    "risk_level": level,
                    "hazard_desc": wo["hazard_desc"],
                    "clause": wo["clause"], "requirement": wo["requirement"],
                },
                "worker_notice": wo["worker_notice"],
            }
            _goto_task(tid, {"scene": scene_t2, "area": location,
                             "report_type": "text"}, next_page="report",
                       result={"status": "success", "payload": payload})


def _tab3_lookup() -> None:
    st.caption("只读查询：按工单号快速定位。读写硬隔离——本入口不做任何写操作"
               "（见方案文档 5.2）。")
    conn = get_conn()
    init_db(conn)
    rows = get_conn().execute(
        "SELECT w.id, w.risk_level, w.status, t.source "
        "FROM work_orders w LEFT JOIN tasks t ON t.id=w.task_id "
        "ORDER BY w.created_at DESC LIMIT 200").fetchall()
    if not rows:
        st.info("暂无工单记录")
        return
    q = st.text_input("筛选：工单号片段或状态关键词", "", key="lk_q").strip().lower()
    shown = [r for r in rows if not q or q in r["id"].lower()
             or q in (r["status"] or "").lower()]
    st.caption(f"共 {len(shown)} 条（最近优先）")
    for r in shown[:30]:
        st.markdown(f"- `{r['id']}`　{r['risk_level']}｜{r['status']}"
                    f"｜来源 {r['source'] or 'upload'}")


@safe_page("统一上报")
def render_upload() -> None:
    st.title("📤 统一上报")
    conn = get_conn()
    init_db(conn)
    svc = TaskService(conn)

    t_media, t_text, t_look = st.tabs(["📷 影像研判", "📝 文字 / 🎤 语音上报", "🔍 工单速查"])
    with t_media:
        _tab1_media(svc)
    with t_text:
        _tab2_text_voice(svc)
    with t_look:
        _tab3_lookup()


def _goto_task(task_id: str, permit_info: dict, next_page: str,
               result: dict | None = None) -> None:
    """设置会话并驱动导航（供两个 Tab 的提交通路复用）。

    result 非空（文字建单合成的轻量研判结果）则直接注入；否则清掉旧结果，
    让影像链路进入 agents 页从头跑。
    """
    st.session_state["current_task_id"] = task_id
    st.session_state["permit_info"] = permit_info
    st.session_state["_ran"] = False
    if result is None:
        st.session_state.pop("_result", None)
    else:
        st.session_state["_result"] = result
    st.session_state["_nav_page"] = next_page
    st.rerun()
