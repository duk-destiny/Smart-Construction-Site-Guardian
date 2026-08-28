"""页面：我的整改单（v0.2，responsible 整改责任人专属）。

列出派给本人的工单：查看要求与截止 → 填整改说明 + 拍照上传 → 申请验收；
被驳回的单子展示原因并可重新提交。所有写操作均由显式按钮触发，
对话/意图入口不可达本页（读写硬隔离，见方案文档 5.2）。
"""
from __future__ import annotations


import streamlit as st
from ui.page_helpers import safe_page

from core.evidence import save_rectification_photo
from dao.db import get_conn, init_db
from services.audit_service import AuditService
from services.dispatch_service import DispatchService
from services.permission_service import PermissionError as ServicePermissionError

_STATUS_TAG = {
    "open": "🔨 待整改",
    "rejected": "↩️ 已驳回 · 待重改",
    "submitted": "⏳ 待验收",
    "closed": "✅ 已销项",
}


def _fmt_deadline(deadline: str | None) -> str:
    if not deadline:
        return "未设截止"
    from datetime import datetime, timezone
    try:
        dl = datetime.strptime(deadline[:19], "%Y-%m-%d %H:%M:%S") \
            .replace(tzinfo=timezone.utc)
        remain = dl - datetime.now(timezone.utc)
        hours = remain.total_seconds() / 3600
        if hours < 0:
            return f"{deadline[:19]}（⚠️ 已逾期 {-hours:.0f}h）"
        return f"{deadline[:19]}（剩 {hours:.0f}h）"
    except ValueError:
        return deadline


@safe_page("我的整改单")
def render_my_orders() -> None:
    st.title("🧰 我的整改单")

    user_id = st.session_state.get("user_id")
    if not user_id or st.session_state.get("role") != "responsible":
        st.info("该页面向「整改责任人」账号开放（演示账号 lisi / demo1234）。")
        return

    conn = get_conn()
    init_db(conn)
    svc = DispatchService(conn)
    orders = svc.orders.list_by_assignee(user_id)
    username = st.session_state.get("username", "")

    if not orders:
        st.success(f"{username}，当前没有待处理的整改单 👍")
        return
    st.caption(f"共 {len(orders)} 张待处理工单（按创建时间倒序）")

    for order in orders:
        tag = _STATUS_TAG.get(order["status"], order["status"])
        desc = (order["hazard_desc"] or "")[:32]
        with st.expander(
            f"[{tag}] {order['risk_level']} ｜ {order['created_at'][:19]} ｜ {desc}"
        ):
            c1, c2 = st.columns(2)
            c1.write(f"**工单号**：{order['id']}")
            c1.write(f"**任务号**：{order['task_id']}")
            c1.write(f"**风险等级**：{order['risk_level']}")
            c2.write(f"**截止**：{_fmt_deadline(order['deadline'])}")
            c2.write(f"**派发时间**：{(order['dispatched_at'] or '—')[:19]}")
            st.write(f"**隐患描述**：{order['hazard_desc']}")
            st.write(f"**违反规范**：{order['clause'] or '—'}")
            st.write(f"**整改要求**：{order['requirement']}")
            st.info(f"💬 工人白话提示：{order['worker_notice'] or '—'}")

            if order["status"] == "submitted":
                st.warning("已提交验收，等待安全员/管理员复核。")
                continue

            if order["status"] == "rejected":
                st.error(f"❌ 验收驳回原因:{order['review_reason'] or '未填写'}——请整改后重新提交。")

            note = st.text_area(
                "整改说明", value=order["submitted_note"] or "",
                placeholder="简述整改措施、完成时间与现场情况",
                key=f"note_{order['id']}", height=90)
            uploads = st.file_uploader(
                "整改现场照片（可多选）", type=["jpg", "jpeg", "png"],
                accept_multiple_files=True, key=f"up_{order['id']}")

            if st.button("📤 提交整改并申请验收", key=f"submit_{order['id']}",
                         use_container_width=True):
                paths: list[str] = []
                for f in uploads or []:
                    path = save_rectification_photo(
                        order["id"], f.name, f.getvalue())
                    if path:
                        paths.append(path)
                try:
                    svc.submit_rectification(order["id"], user_id, note, paths)
                except ServicePermissionError as e:
                    st.error(f"权限不足：{e}")
                except ValueError as e:
                    st.error(str(e))
                else:
                    AuditService(conn).append(
                        user_id, "rectification_submit_view",
                        {"order_id": order["id"], "images": len(paths)})
                    st.success("已提交验收 ✅ 请等待复核结果")
                    st.rerun()
