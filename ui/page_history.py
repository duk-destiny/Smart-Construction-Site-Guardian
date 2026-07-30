"""页面：检测历史与合规分析（B3/B4/B5/B6）。

实时/上传检测记录的追踪、按日期筛选、合规率与类别命中统计（柱状图）、CSV 导出。
查询结果使用 @st.cache_data 缓存，减少切页/重筛选时的加载时间。
"""
from __future__ import annotations

import csv
import io

import streamlit as st

from core.compliance import LEVEL_LABEL
from dao.db import get_conn, init_db
from dao.models import DetectionRecordDAO

_CN = {
    "spark": "火花（动火明火）", "smoke": "烟雾（火情）", "no_helmet": "未佩戴安全帽",
    "no_vest": "未穿反光衣", "face_shield": "未佩戴防护面罩",
    "extinguisher": "灭火器缺失", "flammable": "易燃物未清理",
    "load_object": "堆放物", "load_object_tilted": "堆放物倾斜",
    "helmet": "佩戴安全帽", "vest": "穿着反光衣", "person": "人员",
}


@st.cache_data(ttl=300)
def _stats_by_date(start_s: str | None, end_s: str | None) -> list[dict]:
    """按日聚合（缓存 5 分钟）。"""
    conn = get_conn()
    init_db(conn)
    dao = DetectionRecordDAO(conn)
    return dao.stats_by_date(start_s, end_s)


@st.cache_data(ttl=300)
def _severity_breakdown(start_s: str | None, end_s: str | None) -> list[dict]:
    """类别命中分布（缓存 5 分钟）。"""
    conn = get_conn()
    init_db(conn)
    dao = DetectionRecordDAO(conn)
    return dao.severity_breakdown(start_s, end_s)


@st.cache_data(ttl=300)
def _cached_query(
    start_s: str | None, end_s: str | None,
    severity: str | None, cls: str | None,
) -> list[dict]:
    """检测明细查询（缓存 5 分钟）。"""
    conn = get_conn()
    init_db(conn)
    dao = DetectionRecordDAO(conn)
    return dao.query(start_s, end_s, severity=severity, cls=cls)


def render_history() -> None:
    st.title("📊 检测历史与合规分析")

    c1, c2 = st.columns(2)
    with c1:
        start = st.date_input("起始日期", value=None)
    with c2:
        end = st.date_input("结束日期", value=None)
    start_s = start.isoformat() if start else None
    end_s = end.isoformat() if end else None

    # ── 合规率趋势（B4）──
    by_date = _stats_by_date(start_s, end_s)
    if by_date:
        st.subheader("每日合规率趋势")
        rows = [{
            "日期": r["day"],
            "不合规": r["non_compliant"], "警告": r["warning"], "合规": r["compliant"],
        } for r in by_date]
        st.bar_chart(rows, x="日期")
        total_frames = sum(r["non_compliant"] + r["warning"] + r["compliant"] for r in by_date)
        nc = sum(r["non_compliant"] for r in by_date)
        warn_total = sum(r["warning"] for r in by_date)
        comp_total = sum(r["compliant"] for r in by_date)
        rate = (1 - nc / total_frames) * 100 if total_frames else 100.0
        m1, m2, m3 = st.columns(3)
        m1.metric("监测帧数", total_frames)
        m2.metric("不合规帧", nc)
        m3.metric("合规率", f"{rate:.1f}%")

        # 合规级别分布（B4 柱状图）
        st.subheader("合规级别分布")
        st.bar_chart(
            [{"合规级别": "不合规", "帧数": nc},
             {"合规级别": "警告", "帧数": warn_total},
             {"合规级别": "合规", "帧数": comp_total}],
            x="合规级别", y="帧数")

    # ── 类别命中分布（B4 柱状图）──
    brk = _severity_breakdown(start_s, end_s)
    if brk:
        st.subheader("隐患类别命中分布")
        sev_rows = [{"类别": _CN.get(b["cls"], b["cls"]), "命中次数": b["cnt"]}
                     for b in brk]
        st.bar_chart(sev_rows, x="类别", y="命中次数")

    # ── 明细列表 + 筛选（B5）──
    st.subheader("检测明细")
    sev_filter = st.selectbox("按严重度筛选", ["全部", "critical", "warning", "safe"])
    sev_arg = None if sev_filter == "全部" else sev_filter
    cls_filter = st.text_input("按类别筛选（隐患键，可空）", "")
    records = _cached_query(start_s, end_s, severity=sev_arg,
                            cls=cls_filter.strip() or None)

    if records:
        # 导出 CSV（B6）
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["时间", "会话", "场景", "合规级别", "类别", "置信度", "严重度"])
        for r in records:
            w.writerow([r["created_at"], r["session_id"], r["scene_id"],
                        r["frame_status"], r["cls"], r["conf"], r["severity"]])
        st.download_button("⬇ 导出 CSV", buf.getvalue(),
                           file_name="detection_records.csv", mime="text/csv")

        for r in records[:200]:
            tag = LEVEL_LABEL.get(r["severity"], r["severity"])
            st.caption(f"{r['created_at']} ｜ {r['scene_id']} ｜ "
                       f"{r['frame_status']} ｜ {_CN.get(r['cls'], r['cls'])} "
                       f"({r['conf']:.2f})")
    else:
        st.info("暂无记录")
