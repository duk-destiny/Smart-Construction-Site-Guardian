"""页面5：管理端页（page_admin，仅 admin）。"""
from __future__ import annotations

import streamlit as st

from dao.db import get_conn, init_db
from services.audit_service import AuditService
from services.kb_admin import KbAdmin


def render_admin() -> None:
    st.title("🛠 管理端（仅管理员）")
    if st.session_state.get("role") != "admin":
        st.error("无权限访问管理端")
        return

    st.subheader("导入规范 PDF")
    pdf = st.file_uploader("选择规范 PDF", type=["pdf"])
    if pdf and st.button("解析入库"):
        import os
        os.makedirs("data/kb", exist_ok=True)
        path = os.path.join("data/kb", pdf.name)
        with open(path, "wb") as f:
            f.write(pdf.getbuffer())
        conn = get_conn()
        init_db(conn)
        res = KbAdmin(conn).import_pdf(path, st.session_state.get("user_id", "admin"))
        if res.get("ok"):
            st.success(f"入库成功，切分 {res['chunks']} 块")
            AuditService(conn).append(st.session_state.get("user_id"), "import_pdf",
                                     {"filename": pdf.name, "chunks": res["chunks"]})
        else:
            st.error(res.get("error", "导入失败"))

    st.divider()
    st.subheader("全量隐患记录")
    conn = get_conn()
    init_db(conn)
    rows = conn.execute(
        "SELECT task_id, risk_level, hazard_desc, created_at FROM v_task_summary "
        "WHERE hazard_desc IS NOT NULL ORDER BY created_at DESC LIMIT 100").fetchall()
    if rows:
        st.dataframe([dict(r) for r in rows])
    else:
        st.caption("暂无记录")

    st.divider()
    st.subheader("操作审计日志")
    logs = conn.execute(
        "SELECT user_id, action, detail_json, created_at FROM audit_logs "
        "ORDER BY created_at DESC LIMIT 200").fetchall()
    if logs:
        st.dataframe([dict(r) for r in logs])
    else:
        st.caption("暂无日志")
