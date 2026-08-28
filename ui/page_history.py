"""页面：检测历史与合规分析（B3/B4/B5/B6）。

实时/上传检测记录的追踪、按日期筛选、合规率与类别命中统计（柱状图）、CSV 导出。
查询结果使用 @st.cache_data 缓存，减少切页/重筛选时的加载时间（缓存的是
services.history_service 返回的 dict 列表，连接与 SQL 全在服务层）。

注意：本页包含两个层级的数据——
- 任务风险一览：来自 work_orders + risks（Agent 综合研判，值与报告页一致）
- 检测明细：来自 detection_records（帧/目标级严重度，纯视觉，粒度更细）
"""
from __future__ import annotations

import csv
import io

import streamlit as st
from ui.page_helpers import safe_page

from services import history_service

_CN = {
    "spark": "火花（动火明火）", "smoke": "烟雾（火情）", "no_helmet": "未佩戴安全帽",
    "no_vest": "未穿反光衣", "face_shield": "防护面罩",
    "extinguisher": "灭火器", "flammable": "易燃物未清理",
    "helmet": "佩戴安全帽", "vest": "穿着反光衣", "person": "人员",
}

RISK_EMOJI = {"重大": "🔴", "较大": "🟠", "一般": "🟡", "低": "🟢"}


@st.cache_data(ttl=300)
def _stats_by_date(start_s: str | None, end_s: str | None) -> list[dict]:
    """按日聚合（缓存 5 分钟）。"""
    return history_service.stats_by_date(start_s, end_s)


@st.cache_data(ttl=300)
def _severity_breakdown(start_s: str | None, end_s: str | None) -> list[dict]:
    """类别命中分布（缓存 5 分钟）。"""
    return history_service.severity_breakdown(start_s, end_s)


@st.cache_data(ttl=300)
def _cached_query(
    start_s: str | None, end_s: str | None,
    severity: str | None, cls: str | None,
) -> list[dict]:
    """检测明细查询（缓存 5 分钟）。"""
    return history_service.query_records(start_s, end_s, severity=severity,
                                         cls=cls)


@st.cache_data(ttl=300)
def _task_risks(start_s: str | None = None, end_s: str | None = None) -> list[dict]:
    """任务级风险一览（缓存 5 分钟），与报告页同源。"""
    return history_service.task_risks(start_s, end_s)


@safe_page("检测历史与分析")
def render_history() -> None:
    st.title("📊 检测历史与合规分析")

    c1, c2 = st.columns(2)
    with c1:
        start = st.date_input("起始日期", value=None)
    with c2:
        end = st.date_input("结束日期", value=None)
    start_s = start.isoformat() if start else None
    end_s = end.isoformat() if end else None

    # ═══════════════════════════════════════════
    # 任务风险一览（与报告页同源的任务级风险）
    # ═══════════════════════════════════════════
    task_rows = _task_risks(start_s, end_s)
    if task_rows:
        st.subheader("🔍 任务风险一览（Agent 综合研判，与整改工单页一致）")
        for row in task_rows:
            level = row["override_level"] or row["auto_level"] or row["wo_risk_level"] or "—"
            emoji = RISK_EMOJI.get(level, "⚪")
            desc = (row["hazard_desc"] or "")[:50]
            if len(row["hazard_desc"] or "") > 50:
                desc += "…"
            override_tag = " ✎已改判" if row["override_level"] else ""
            label = f"{emoji} [{level}]{override_tag}  {row['task_id']}  —  {desc}"
            with st.expander(label):
                st.write(f"**任务编号**：{row['task_id']}")
                st.write(f"**风险等级**：{level}")
                st.write(f"**隐患描述**：{row['hazard_desc'] or '—'}")
                st.write(f"**时间**：{row['created_at']}")
                if row["override_level"]:
                    st.warning(f"人工改判 → {row['override_level']}"
                               f"（原因：{row['override_reason']}）")
    else:
        st.info("暂无任务风险记录")

    st.divider()

    # ═══════════════════════════════════════════
    # 合规率趋势（B4）
    # ═══════════════════════════════════════════
    by_date = _stats_by_date(start_s, end_s)
    if by_date:
        # 指标卡先行渲染（快），3 个 bar_chart 收在 toggle 后按需渲染（默认关，跳过执行）
        total_frames = sum(r["non_compliant"] + r["warning"] + r["compliant"] for r in by_date)
        nc = sum(r["non_compliant"] for r in by_date)
        warn_total = sum(r["warning"] for r in by_date)
        comp_total = sum(r["compliant"] for r in by_date)
        rate = (1 - nc / total_frames) * 100 if total_frames else 100.0
        m1, m2, m3 = st.columns(3)
        m1.metric("监测帧数", total_frames)
        m2.metric("不合规帧", nc)
        m3.metric("合规率", f"{rate:.1f}%")

        show_charts = st.toggle("📈 显示图表分析", value=False)
        if show_charts:
            st.subheader("每日合规率趋势")
            rows = [{
                "日期": r["day"],
                "不合规": r["non_compliant"], "警告": r["warning"], "合规": r["compliant"],
            } for r in by_date]
            st.bar_chart(rows, x="日期")

            st.subheader("合规级别分布")
            st.bar_chart(
                [{"合规级别": "不合规", "帧数": nc},
                 {"合规级别": "警告", "帧数": warn_total},
                 {"合规级别": "合规", "帧数": comp_total}],
                x="合规级别", y="帧数")

            brk = _severity_breakdown(start_s, end_s)
            if brk:
                st.subheader("隐患类别命中分布")
                sev_rows = [{"类别": _CN.get(b["cls"], b["cls"]), "命中次数": b["cnt"]}
                             for b in brk]
                st.bar_chart(sev_rows, x="类别", y="命中次数")

    # ═══════════════════════════════════════════
    # 检测明细（B5）— 帧/目标级严重度
    # ═══════════════════════════════════════════
    st.subheader("检测明细（帧/目标级）")
    st.caption("💡 以下为每帧每个检测目标的级别，粒度比上方的任务风险更细。"
               "任务风险「重大」≠ 每一帧都违规，反之亦然。")

    SEV_CN = {"critical": "不合规", "warning": "警告", "safe": "合规"}
    sev_filter = st.selectbox("按目标级别筛选",
                              ["全部", "critical（不合规）", "warning（警告）", "safe（合规）"])
    if "（" in sev_filter:
        sev_arg = sev_filter.split("（")[0]  # 提取原始键
    else:
        sev_arg = None

    cls_filter = st.text_input("按类别筛选（隐患键，可空）", "")
    records = _cached_query(start_s, end_s, severity=sev_arg,
                            cls=cls_filter.strip() or None)

    if records:
        # 导出 CSV（B6）
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["时间", "会话", "场景", "帧合规级别", "类别", "置信度",
                    "目标级别", "目标ID", "连续帧"])
        for r in records:
            w.writerow([r["created_at"], r["session_id"], r["scene_id"],
                        r["frame_status"], r["cls"], r["conf"],
                        SEV_CN.get(r["severity"], r["severity"]),
                        r.get("track_id") or "", r.get("track_frames") or ""])
        st.download_button("⬇ 导出 CSV", buf.getvalue(),
                           file_name="detection_records.csv", mime="text/csv")

        for r in records[:200]:
            sev_cn = SEV_CN.get(r["severity"], r["severity"])
            st.caption(f"{r['created_at']} ｜ {r['scene_id'] or '—'} ｜ "
                       f"帧{r['frame_status']} ｜ {_CN.get(r['cls'], r['cls'])} "
                       f"({r['conf']:.2f}) ｜ 目标级别：{sev_cn} ｜ "
                       f"track {r.get('track_id') or '—'} 连续 "
                       f"{r.get('track_frames') or 1} 帧")
    else:
        st.info("暂无检测记录")
