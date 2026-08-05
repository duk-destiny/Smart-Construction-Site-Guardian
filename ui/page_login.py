"""页面1：登录页（page_login）。

交互：用户名/密码 → AuthService.login；失败标红+审计；成功按 role 写入 session_state 并跳转上传页。
"""
from __future__ import annotations

import streamlit as st
from ui.page_helpers import safe_page

from dao.db import get_conn, init_db
from services.auth_service import AuthService


@safe_page("登录")
def render_login() -> None:
    st.title("🌊 海之子 · 动火作业安全智能体")
    st.caption("本地离线 · 多Agent 安全研判")

    with st.form("login_form"):
        username = st.text_input("用户名")
        password = st.text_input("密码", type="password")
        submitted = st.form_submit_button("登录")

    if submitted:
        conn = get_conn()
        init_db(conn)
        svc = AuthService(conn)
        res = svc.login(username, password)
        if res.get("ok"):
            st.session_state["user_id"] = res["user_id"]
            st.session_state["role"] = res["role"]
            st.session_state["username"] = username
            st.success(f"欢迎，{username}（{res['role']}）")
            st.rerun()
        else:
            st.error("用户名或密码错误")
