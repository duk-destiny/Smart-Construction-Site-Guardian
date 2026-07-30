"""Streamlit 入口：使用原生 st.navigation 进行多页导航，减少切页模糊与状态不同步。

依赖方向：ui → services → agents → core → dao（代码规范 §3）。
启动：streamlit run app.py --server.address 0.0.0.0 --server.port 8501
"""
from __future__ import annotations

import streamlit as st

import ui.page_agents as page_agents
import ui.page_admin as page_admin
import ui.page_history as page_history
import ui.page_login as page_login
import ui.page_realtime as page_realtime
import ui.page_report as page_report
import ui.page_upload as page_upload
import ui.theme as theme

st.set_page_config(page_title="海之子·动火安全智能体", layout="wide")
theme.apply_theme()


def main() -> None:
    st.session_state.setdefault("role", None)

    if not st.session_state["role"]:
        page_login.render_login()
        return

    role = st.session_state["role"]
    username = st.session_state.get("username", "未知用户")

    pages: list[st.Page] = [
        st.Page(page_upload.render_upload, title="上传与作业票", icon="📤", default=True),
        st.Page(page_realtime.render_realtime, title="实时摄像头监测", icon="📷"),
        st.Page(page_agents.render_agents, title="多Agent研判", icon="🤖"),
        st.Page(page_report.render_report, title="工单/改判/导出", icon="📋"),
        st.Page(page_history.render_history, title="检测历史与分析", icon="📊"),
    ]
    if role == "admin":
        pages.append(st.Page(page_admin.render_admin, title="管理端", icon="⚙️"))

    pg = st.navigation(pages, position="sidebar")
    pg.run()

    # 用户状态与退出放在主内容区顶部，不占用侧边栏，避免折叠冲突
    _, right, _ = st.columns([1, 2, 1])
    with right:
        with st.container():
            c1, c2 = st.columns([3, 1])
            c1.caption(f"👤 {username}（{role}）")
            if c2.button("🚪 退出", use_container_width=True, key="_logout_top"):
                for k in ("role", "user_id", "username", "current_task_id",
                          "permit_info", "_result", "_ran", "_rt_last",
                          "_rt_frames", "_rt_violations", "_realtime_session"):
                    st.session_state.pop(k, None)
                st.rerun()


if __name__ == "__main__":
    main()
