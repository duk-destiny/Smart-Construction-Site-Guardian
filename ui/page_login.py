"""页面1：登录页（page_login）。

交互：用户名/密码 → AuthService.login；失败标红+审计；成功按 role 写入 session_state 并跳转上传页。
v0.8 账号治理：
- 初始密码未改（must_change_password=1）时按 security.force_default_pwd_change
  二态处理：true → 强制先改密再进系统（本页内联改密表单）；false → 放行并设
  pwd_warning 提醒标记（app 顶栏常驻提醒）。
"""
from __future__ import annotations

import streamlit as st
from ui.page_helpers import safe_page

from core.config import ConfigLoader
from dao.db import get_conn, init_db
from services.auth_service import AuthService


def _force_pwd_change() -> bool:
    """读门控开关；配置缺失按 false（不阻断演示）。"""
    try:
        return bool((ConfigLoader().get("security") or {})
                    .get("force_default_pwd_change", False))
    except Exception:  # noqa: BLE001 配置不可用时按提醒模式
        return False


def _render_forced_change(svc: AuthService, pending: dict) -> None:
    """首登强制改密表单：验证原密码成功即清标记放行。"""
    st.warning("该账号仍在使用初始密码，首次登录必须修改密码后才能进入系统。")
    with st.form("force_change_form"):
        old_pwd = st.text_input("原密码", type="password")
        new_pwd = st.text_input("新密码（至少 8 位）", type="password")
        confirm = st.text_input("确认新密码", type="password")
        submitted = st.form_submit_button("修改密码并进入系统", type="primary")
    if submitted:
        if new_pwd != confirm:
            st.error("两次输入的新密码不一致")
            return
        res = svc.change_password(pending["user_id"], old_pwd, new_pwd)
        if res.get("ok"):
            st.session_state["user_id"] = pending["user_id"]
            st.session_state["role"] = pending["role"]
            st.session_state["username"] = pending["username"]
            st.session_state.pop("_pending_pwd_user", None)
            st.success("密码已更新，欢迎进入系统")
            st.rerun()
        else:
            st.error(res.get("error", "修改失败"))


@safe_page("登录")
def render_login() -> None:
    st.title("🌊 智护工地 · 施工安全智能体")
    st.caption("本地离线 · 多Agent 安全研判")

    pending = st.session_state.get("_pending_pwd_user")
    if pending:
        _render_forced_change(AuthService(get_conn()), pending)
        return

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
            if res.get("must_change_password") and _force_pwd_change():
                st.session_state["_pending_pwd_user"] = {
                    "user_id": res["user_id"], "role": res["role"],
                    "username": username}
                st.rerun()
            st.session_state["user_id"] = res["user_id"]
            st.session_state["role"] = res["role"]
            st.session_state["username"] = username
            # 提醒模式：初始密码未改，顶栏常驻提醒（不阻断演示）
            st.session_state["pwd_warning"] = bool(res.get("must_change_password"))
            st.success(f"欢迎，{username}（{res['role']}）")
            st.rerun()
        else:
            st.error("用户名或密码错误")
