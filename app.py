"""Streamlit 入口：依据 session_state.role 路由 5 个页面（M07）。

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

# 站点导航（仅登录后显示）
NAV = {
    "login": ("登录", page_login.render_login),
    "upload": ("上传与作业票", page_upload.render_upload),
    "realtime": ("实时摄像头监测", page_realtime.render_realtime),
    "agents": ("多Agent研判", page_agents.render_agents),
    "report": ("工单/改判/导出", page_report.render_report),
    "history": ("检测历史与分析", page_history.render_history),
    "admin": ("管理端", page_admin.render_admin),
}


def main() -> None:
    st.session_state.setdefault("role", None)

    if not st.session_state["role"]:
        page_login.render_login()
        return

    role = st.session_state["role"]
    options: list[str] = ["upload", "realtime", "agents", "report", "history"] \
        + (["admin"] if role == "admin" else [])
    current_page = st.session_state.get("page", "upload")

    # 侧边导航：用 button 替代 radio，消除 radio 组件双态切换导致的模糊/跳闪
    with st.sidebar:
        st.markdown("### 导航")
        for key in options:
            label, _ = NAV[key]
            is_active = (key == current_page)
            # 激活项用 filled primary 样式，非激活项用 border 样式
            btn_type = "primary" if is_active else "secondary"
            if st.button(label, key=f"nav_{key}", type=btn_type,
                         use_container_width=True, icon="📍" if is_active else None):
                if key != current_page:
                    st.session_state["page"] = key
                    st.rerun()
        st.divider()
        st.write(f"当前用户：{st.session_state.get('username')}（{role}）")
        if st.button("退出登录", key="logout_btn", type="secondary",
                     use_container_width=True):
            for k in ("role", "user_id", "username", "current_task_id",
                      "permit_info", "_result", "_ran"):
                st.session_state.pop(k, None)
            st.rerun()

    NAV[current_page][1]()


if __name__ == "__main__":
    main()
