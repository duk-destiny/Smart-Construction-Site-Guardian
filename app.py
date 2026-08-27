"""Streamlit 入口：使用原生 st.navigation 进行多页导航，减少切页模糊与状态不同步。

依赖方向：ui → services → agents → core → dao（代码规范 §3）。
启动：streamlit run app.py --server.address 0.0.0.0 --server.port 8501
"""
from __future__ import annotations

import os
# 纯 CPU 部署 + 线程收敛：在导入 torch/onnxruntime 前禁用 CUDA 并限制线程数，
# 对齐 scripts/e2e_apptest.py 已验证稳定的崩溃抑制配置（onnxruntime 多线程原生崩溃）
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("TQDM_DISABLE", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import streamlit as st
# 注意：主进程不 import torch —— torch + onnxruntime 同进程多线程会触发原生段错误。
# BGE/torch 已隔离到独立子进程（core.bge_worker），主进程零 torch 痕迹。
import ui.page_agents as page_agents
import ui.page_admin as page_admin
import ui.page_history as page_history
import ui.page_login as page_login
import ui.page_my_orders as page_my_orders
import ui.page_realtime as page_realtime
import ui.page_report as page_report
import ui.page_upload as page_upload
import ui.page_diag as page_diag
import ui.theme as theme

st.set_page_config(
    page_title="智护工地 · 施工安全智能体",
    layout="wide",
    initial_sidebar_state="expanded",
)
theme.apply_theme()


def main() -> None:
    # 首次启动自举：建库 + 种子默认账号，保证 clone 后开箱即登录
    try:
        from core.bootstrap import ensure_initialized, ensure_models
        ensure_initialized()
        ensure_models()
    except Exception:
        pass

    st.session_state.setdefault("role", None)

    if not st.session_state["role"]:
        page_login.render_login()
        return

    role = st.session_state["role"]
    username = st.session_state.get("username", "未知用户")

    # —— 启动期一次性工作：BGE 预热 / LLM 预热 / RTSP 监控，只在登录后首次 rerun 跑一次，
    #    之后切页重跑 app.py 时跳过，省掉每次切页重复构造 LlmEngine + 起线程 + 读 config 的开销。
    # —— 异步预热：登录后立刻渲染页面，后台单线程按优先级预热，不阻塞首屏 ——
    # 优先级：monitor（RTSP 轮询，秒级）→ LLM warmup（ollama 常驻）→ BGE（~6s，RAG 要用）
    # 预热完成前 RAG 自动降级跳过（rag_engine 已有 _MODEL is None 兜底）
    if not st.session_state.get("_boot_done"):
        st.session_state["_boot_done"] = True

        def _background_prewarm():
            # BGE/torch 已隔离到独立子进程（core.bge_worker），主进程零 torch，
            # 从根本上消除 torch+onnxruntime 同进程原生段错误。
            # 0) YOLO 双场景检测头预热（v0.6）：首请求不再付首次建会话的 1-3s，
            #    预热失败由首请求按需重试（_get_engine 模块级单例兜底）
            try:
                from ui.page_realtime import _prewarm_engine
                _prewarm_engine()
            except Exception:
                pass
            # 1) RTSP 后台监控：幂等，未启用则秒返回
            try:
                from services import monitor_service
                monitor_service.ensure_monitor_started()
            except Exception:
                pass
            # 2) LLM warmup：ollama 独立进程 keep_alive 常驻，best-effort
            try:
                from core.llm_engine import LlmEngine as _Llm
                _Llm().warmup()
            except Exception:
                pass
            # 3) BGE 向量模型：验证可在守护线程安全加载（CUDA 禁用 + torch 单线程配置下）
            try:
                from core.rag_engine import RagEngine
                RagEngine.preload()
            except Exception:
                pass

        import threading as _threading
        _threading.Thread(target=_background_prewarm, daemon=True).start()

    # _nav_page：从 page_upload / page_agents 写入，驱动 st.navigation 显示目标页
    target_page = st.session_state.pop("_nav_page", None)

    if role == "responsible":
        # v0.2 整改责任人：仅开放"我的整改单"，不接触研判/上传/管理端
        page_map = {
            "my_orders": st.Page(page_my_orders.render_my_orders,
                                 title="我的整改单", icon="🧰"),
        }
    else:
        page_map = {
            "upload": st.Page(page_upload.render_upload, title="统一上报", icon="📤"),
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

    # 用户状态与退出：渲染在 pg.run() 之前，确保始终位于页面内容顶部
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

    pg.run()


if __name__ == "__main__":
    main()
