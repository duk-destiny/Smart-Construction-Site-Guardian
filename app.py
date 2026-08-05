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
import ui.page_diag as page_diag
import ui.theme as theme

st.set_page_config(
    page_title="海之子·动火安全智能体",
    layout="wide",
    initial_sidebar_state="expanded",
)
theme.apply_theme()


def main() -> None:
    st.session_state.setdefault("role", None)

    if not st.session_state["role"]:
        page_login.render_login()
        return

    role = st.session_state["role"]
    username = st.session_state.get("username", "未知用户")

    # 主线程预热 BGE 向量模型：Windows 下 onnxruntime 无法在守护线程首次初始化，
    # 必须在主线程先加载一次，之后告警守护线程复用单例模型做条款 RAG 检索。
    try:
        from core.rag_engine import RagEngine
        RagEngine.preload()
    except Exception:
        pass

    # 后台 RTSP 自动轮询监控：按 config monitor.* 自动启动（幂等，未启用则跳过）
    try:
        from services import monitor_service
        monitor_service.ensure_monitor_started()
    except Exception:
        pass

    # _nav_page：从 page_upload / page_agents 写入，驱动 st.navigation 显示目标页
    target_page = st.session_state.pop("_nav_page", None)

    page_map = {
        "upload": st.Page(page_upload.render_upload, title="上传与作业票", icon="📤"),
        "realtime": st.Page(page_realtime.render_realtime, title="实时摄像头监测", icon="📷"),
        "agents": st.Page(page_agents.render_agents, title="多Agent研判", icon="🤖"),
        "report": st.Page(page_report.render_report, title="工单/改判/导出", icon="📋"),
        "history": st.Page(page_history.render_history, title="检测历史与分析", icon="📊"),
    }
    pages: list[st.Page] = list(page_map.values())
    if role == "admin":
        page_map["admin"] = st.Page(page_admin.render_admin, title="管理端", icon="⚙️")
        pages.append(page_map["admin"])
        page_map["diag"] = st.Page(page_diag.render_diag, title="系统自检", icon="🩺")
        pages.append(page_map["diag"])

    pg = st.navigation(pages, position="sidebar")
    if target_page in page_map:
        st.switch_page(page_map[target_page])
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
