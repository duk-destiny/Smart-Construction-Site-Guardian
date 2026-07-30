"""页面2：上传与作业票录入页（page_upload）。

交互：上传图片/视频 + 作业票表单 → TaskService.create_task → 跳转分步面板（传 task_id）。
"""
from __future__ import annotations

import streamlit as st

from dao.db import get_conn, init_db
from services.task_service import TaskService


def render_upload() -> None:
    st.title("📤 上传现场资料与作业票")
    uploaded = st.file_uploader("现场图片/视频", type=["jpg", "jpeg", "png", "mp4"])
    if uploaded:
        st.image(uploaded, caption="预览", width=320) if uploaded.type.startswith("image") else st.video(uploaded)

    # 作业类型（场景）选择：决定加载哪组检测头与规范库
    scene = st.selectbox("作业类型 / 危险检测场景", ["hot_work", "construction_ppe"],
                         format_func=lambda s: "动火作业安全" if s == "hot_work" else "施工 PPE / 危险检测",
                         index=0)
    st.session_state["scene"] = scene
    st.caption("动火作业安全：火情/火花/烟雾 + 动火规范；"
               "施工 PPE：安全帽/反光衣 + 堆放物倾斜检测（Detecting-danger 独门能力）")

    is_hot = scene == "hot_work"
    st.subheader("作业票信息")
    with st.form("permit_form"):
        if is_hot:
            fire_level = st.selectbox("动火级别", ["一级", "二级"])
            watcher = st.text_input("监火人")
            valid_until = st.text_input("有效期限")
            area = st.text_input("作业区域")
            extinguisher = st.text_input("灭火器配置", "已配备")
            fire_blanket = st.text_input("防火毯", "已设置")
            approval = st.text_input("作业审批", "已审批")
        else:
            fire_level = "—"
            watcher = st.text_input("安全员", "已指定")
            valid_until = st.text_input("有效期限")
            area = st.text_input("作业区域")
            extinguisher = st.text_input("防护装备确认", "已确认")
            fire_blanket = st.text_input("现场清理确认", "已完成")
            approval = st.text_input("作业审批", "已审批")
        submitted = st.form_submit_button("开始智能研判")

    if submitted:
        permit_info = {
            "scene": scene,
            "fire_level": fire_level, "watcher": watcher, "valid_until": valid_until,
            "area": area, "extinguisher": extinguisher,
            "fire_blanket": fire_blanket, "approval": approval,
        }
        conn = get_conn()
        init_db(conn)
        svc = TaskService(conn)
        tid = svc.create_task(st.session_state.get("user_id", "u_demo"), [], permit_info)
        if uploaded:
            import os
            save_dir = "data/uploads"
            os.makedirs(save_dir, exist_ok=True)
            path = os.path.join(save_dir, f"{tid}_{uploaded.name}")
            with open(path, "wb") as f:
                f.write(uploaded.getbuffer())
            st.session_state["uploaded_path"] = path
        st.session_state["current_task_id"] = tid
        st.session_state["permit_info"] = permit_info
        st.session_state["_ran"] = False
        st.session_state.pop("_result", None)
        st.session_state["page"] = "agents"
        st.rerun()
