"""页面2：统一上报（page_upload）——v0.4 按方案文档 4.4 原地升级为三个 Tab。

Tab① 影像研判：原有 图片/视频 + 作业票 → create_task(source=upload) → 重链路；
Tab② 文字/语音上报：自由文本线索直接建单（source=text，跳过视觉链路，
    风险按 compliance.severity 查表），语音为可选转写调用——
    未配置 `asr.*` 时入口**完全不渲染**（静默，用户约定）；
Tab③ 工单速查：输入工单号直达详情（跳报告页历史记录，只读）。
Phase 0：任务创建/媒体落盘/速查查询收口到 services.task_entry 与
services.lookup_service，本页零 get_conn/DAO。
"""
from __future__ import annotations

import streamlit as st
from ui.page_helpers import safe_page

from core.compliance import SEVERITY
from core.yolo_engine import WHITELIST_CN
from services import lookup_service, task_entry


def _tab1_media() -> None:
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
        tid, media_rel, err = task_entry.create_media_task(
            st.session_state.get("user_id"), permit_info, uploaded)
        if err:
            st.error(err)
            return
        if media_rel:
            st.session_state["uploaded_path"] = media_rel
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


def _tab2_text_voice() -> None:
    st.caption("适合摄像头拍不到的隐患（无证上岗、无交底、通道占用等）。"
               "提交后**跳过视觉研判**，风险按规则查表定级，直接进入派发闭环。")

    scene_t2 = st.selectbox("归属场景", ["hot_work", "construction_ppe"],
                            format_func=lambda s: "动火作业安全" if s == "hot_work"
                            else "施工 PPE / 危险检测",
                            index=0, key="t2_scene")
    options = _hazard_options()

    # —— AI 提取预填（v0.6）：Provider 链（v0.8 多 base）均未配置时整块静默 ——
    from services.enhance_service import EnhanceEngine
    _enh = EnhanceEngine()
    _prov = _enh.available()
    if _prov:
        _tag = (f"📦 {_prov}" if _prov == "local" or "local" in _prov.lower()
                else f"⛅ {_prov}")
        if st.button(f"⚡ AI 提取预填（{_tag}）", key="btn_ai_extract",
                     help="把自然语言拆成类别/位置等字段填入下方，"
                           "提交前请人工确认；结果不影响定级。"):
            if not st.session_state.get("t2_desc_raw"):
                st.warning("请先在下方输入隐患描述，再点 AI 提取")
            else:
                out = _enh.extract_hazard(st.session_state["t2_desc_raw"])
                if out:
                    st.session_state["t2_desc"] = out["description"]
                    st.session_state["t2_hazard"] = out["hazard_key"]
                    st.session_state["t2_area"] = out["location"] or ""
                    st.session_state["t2_scene"] = out["scene_id"]
                    st.success(f"AI 预填完成（{_tag}），请人工确认后提交")
                    st.rerun()
                else:
                    st.warning(f"AI 提取暂不可用：{_enh.last_error or '未知原因'}，"
                               "请手动填写。")

    # —— 语音入口：未配置 asr.* 时整块不渲染（静默，用户约定）——
    if task_entry.asr_available():
        audio = st.audio_input("🎤 或先说一段话，转写后自动填入下方文本框")
        ca, cb = st.columns([1, 3])
        if ca.button("🎤 转写", key="btn_asr"):
            if audio is None:
                st.warning("请先录音")
            else:
                text, err = task_entry.asr_transcribe(
                    audio.getvalue(), getattr(audio, "name", None) or "record.wav")
                if text:
                    st.session_state["t2_desc"] = text
                    st.success("转写完成，已填入描述（可修改后提交）")
                    st.rerun()
                else:
                    st.warning(f"转写暂不可用：{err or '未知原因'}，"
                               "可直接手填。")

    st.session_state["t2_desc_raw"] = st.session_state.get("t2_desc", "")
    desc = st.text_area(
        "隐患描述", height=90, key="t2_desc",
        placeholder="例：3号楼西侧电焊机旁堆着纸箱没人清理，也没有监火人")
    hkey = st.selectbox("隐患类别（可后续在派发面板改派时调整）", options,
                        format_func=_option_label, key="t2_hazard")
    location = st.text_input("位置（可选）", key="t2_area",
                             placeholder="如：3号楼西侧 / 地库B区")

    if st.button("📝 创建文字隐患单", type="primary", use_container_width=True):
        res = task_entry.create_text_hazard(
            st.session_state.get("user_id"), desc, hkey,
            scene_id=scene_t2, location=location)
        if not res.get("ok"):
            st.error(res.get("error", "创建失败"))
            return
        tid = res["task_id"]
        payload = {
            "risk_level": res["risk_level"],
            "vision": {"payload": {"detections": []}},
            "work_order": res["work_order"],
            "worker_notice": res["worker_notice"],
        }
        _goto_task(tid, {"scene": scene_t2, "area": location,
                         "report_type": "text"}, next_page="report",
                   result={"status": "success", "payload": payload})


def _tab3_lookup() -> None:
    """只读对话式查询（P3，v0.5）：规则优先 → LLM 兜底 → 人工点选。"""
    st.caption("只读查询：支持「#w_xxx 进度」「3号工单怎么样了」「近7天逾期」"
               "「本周统计」等说法；不提供任何写操作（读写硬隔离）。")
    from datetime import date, timedelta
    from services.dispatch_service import _now_str

    text = st.text_input("问一句", "", key="lk_q",
                         placeholder="如：最近有没有逾期的？w_123 的进度？")
    if not text.strip():
        res = lookup_service.list_view()
        if res:
            st.caption("最新待办工单（输入问题可精确查询）")
            for r in res:
                st.markdown(f"- `{r['id']}`　{r['risk_level']}｜{r['status']}"
                            f"｜责任人 {r['assignee_name'] or '—'}")
        else:
            st.info("暂无工单记录")
        return

    route = lookup_service.route(text)
    if route.tier == "llm":
        st.caption("🤖 已理解（本地模型）")

    if route.action == "order_detail" and route.order_id:
        card = lookup_service.detail_view(route.order_id)
        if card is None:
            st.error(f"未找到工单 {route.order_id}")
            return
        c1, c2, c3 = st.columns(3)
        c1.metric("状态", {"open": "🔨 待整改", "rejected": "↩️ 已驳回",
                           "submitted": "⏳ 待验收",
                           "closed": "✅ 已销项"}.get(card["status"],
                                                      card["status"]))
        c2.metric("责任人", card["assignee_name"] or "未派发")
        c3.metric("截止", (card["deadline"] or "—")[:19])
        st.write(f"**隐患描述**：{card['hazard_desc']}")
        st.write(f"**整改要求**：{card['requirement']}")
        return

    if route.action in ("order_detail", "confirm_list") and route.candidates:
        pick = st.radio("匹配到多张，请选择", route.candidates,
                        format_func=lambda i: f"`{i}`",
                        key="lk_pick")
        card = lookup_service.detail_view(pick)
        if card:
            st.write(f"**状态**：{card['status']}　|　"
                     f"**责任人**：{card['assignee_name'] or '—'}　|　"
                     f"**截止**：{(card['deadline'] or '—')[:19]}")
            st.write(f"**隐患描述**：{card['hazard_desc']}")
        return

    if route.action == "overdue_stats":
        rows = lookup_service.overdue_rows(_now_str())
        st.metric("存量逾期未整改", len(rows))
        for r in rows[:20]:
            st.markdown(f"- `{r['id']}`　{r['risk_level']}｜截止 "
                        f"{(r['deadline'] or '—')[:19]}｜"
                        f"{r['assignee_name'] or '未派发'}｜{(r['hazard_desc'] or '')[:24]}")
        return

    if route.action == "weekly_stats":
        end = date.today().isoformat()
        start = (date.today() - timedelta(days=route.days - 1)).isoformat()
        s = lookup_service.weekly_stats(start, end)
        m1c, m2c, m3c, m4c = st.columns(4)
        m1c.metric("检测帧", s["frames"])
        m2c.metric("不合规帧", s["bad"])
        m3c.metric("新增工单", s["orders_total"])
        m4c.metric("存量逾期", s["overdue_open_now"])
        for line in s["conclusions"][:3]:
            st.markdown(f"- {line}")
        return

    # unknown / human：兜底展示最近列表 + 提示
    if route.hint:
        st.info(route.hint)
    res = lookup_service.list_view()
    for r in res[:10]:
        st.markdown(f"- `{r['id']}`　{r['risk_level']}｜{r['status']}"
                    f"｜责任人 {r['assignee_name'] or '—'}")


@safe_page("统一上报")
def render_upload() -> None:
    st.title("📤 统一上报")

    t_media, t_text, t_look = st.tabs(["📷 影像研判", "📝 文字 / 🎤 语音上报", "🔍 工单速查"])
    with t_media:
        _tab1_media()
    with t_text:
        _tab2_text_voice()
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
