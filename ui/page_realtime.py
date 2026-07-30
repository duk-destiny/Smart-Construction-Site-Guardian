"""页面：实时摄像头监测（A1/A2/A3/B1/B2）。

零依赖轮询方案（A）：st.camera_input 捕获帧 → 轻链路检测 → 三级合规 + 红框高亮；
不合规时触发 800Hz 声音警报（仅实时态，A2）与 Toast 提示（B2）。
"""
from __future__ import annotations

import base64
import io
import time
import uuid
import wave

import cv2
import numpy as np
import streamlit as st

from core.realtime_engine import RealtimeEngine
from dao.db import get_conn, init_db
from dao.models import DetectionRecordDAO
from ui.components import compliance_banner, severity_summary


# ── 声音警报（A2）：numpy 生成 800Hz 方波 wav → base64 → <audio autoplay> ──
def _alarm_html() -> str:
    if "_alarm_b64" not in st.session_state:
        sr = 8000
        dur = 0.5
        t = np.linspace(0, dur, int(sr * dur), endpoint=False)
        tone = (np.sin(2 * np.pi * 800 * t) * 32767 * 0.6).astype(np.int16)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(tone.tobytes())
        st.session_state["_alarm_b64"] = base64.b64encode(buf.getvalue()).decode()
    b64 = st.session_state["_alarm_b64"]
    return (f'<audio autoplay src="data:audio/wav;base64,{b64}">'
            f'<a href="data:audio/wav;base64,{b64}" download="alarm.wav">下载警报音</a></audio>')


@st.cache_resource
def _get_engine() -> RealtimeEngine:
    return RealtimeEngine()


def _persist(session_id: str, frame_status: str, dets: list[dict]) -> None:
    try:
        conn = get_conn()
        init_db(conn)
        dao = DetectionRecordDAO(conn)
        rows = [{
            "scene_id": d.get("scene"),
            "cls": d.get("cls"),
            "conf": d.get("conf", 0.0),
            "severity": _sev_of(d.get("cls")),
        } for d in dets]
        dao.bulk_insert(session_id, frame_status, rows, mode="realtime")
    except Exception as e:  # noqa: BLE001 历史写入失败不应中断监测
        print(f"[realtime] 历史持久化失败: {e}")


def _sev_of(cls: str | None) -> str:
    from core.compliance import SEVERITY
    return SEVERITY.get(cls, "warning")


def render_realtime() -> None:
    st.title("📷 实时摄像头监测")

    if "_realtime_session" not in st.session_state:
        st.session_state["_realtime_session"] = f"s_{uuid.uuid4().hex[:12]}"

    engine = _get_engine()
    if not engine.available:
        st.error("未加载到任何检测模型，请确认 data/models 下权重文件存在（"
                 "yolov8_fire_smoke.onnx / ppe_yolov8.onnx / yolov3-personload.*）。")
        return

    st.caption("动火作业安全 + 施工 PPE 双场景同时接入（复用现有检测头）。"
               "不合规时自动播放 800Hz 声音警报并弹窗提醒。")
    continuous = st.toggle("连续监控（捕获后自动刷新下一帧）", value=True)

    img = st.camera_input("现场画面", key="realtime_cam")
    if img is None:
        # 展示最近一次结果（轮询刷新时保持可见）
        if "_rt_last" in st.session_state:
            _show_last()
        return

    frame = cv2.imdecode(np.frombuffer(img.getbuffer(), np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        st.warning("帧读取失败，请重试")
        return

    t0 = time.time()
    dets, comp = engine.analyze(frame)
    cost = int((time.time() - t0) * 1000)
    annotated = engine.draw(frame, comp)

    # 会话内计数（轻量，无需查库）
    st.session_state.setdefault("_rt_frames", 0)
    st.session_state.setdefault("_rt_violations", 0)
    st.session_state["_rt_frames"] += 1
    if comp["level"] == "critical":
        st.session_state["_rt_violations"] += 1

    # 持久化（B3）
    _persist(st.session_state["_realtime_session"], comp["status"], dets)

    # 缓存最近结果用于轮询刷新展示
    st.session_state["_rt_last"] = {
        "annotated": annotated, "comp": comp, "dets": dets, "cost": cost,
    }
    _show_last()

    # 不合规：声音警报（A2）+ Toast（B2）
    if comp["level"] == "critical":
        st.toast("⚠️ 检测到高危违规，请立即处置！", icon="⚠️")
        st.markdown(_alarm_html(), unsafe_allow_html=True)
    elif comp["level"] == "warning":
        st.toast("⚠️ 发现需关注项，请尽快整改。", icon="⚠️")

    if continuous:
        time.sleep(0.3)
        st.rerun()


def _show_last() -> None:
    last = st.session_state["_rt_last"]
    annotated = last["annotated"]
    comp = last["comp"]

    # 会话轻量统计
    frames = st.session_state.get("_rt_frames", 0)
    viol = st.session_state.get("_rt_violations", 0)
    m1, m2, m3 = st.columns(3)
    m1.metric("本会话监测帧数", frames)
    m2.metric("不合规帧", viol)
    m3.metric("本帧耗时", f"{last['cost']} ms")

    # 三级合规状态横幅（B1 红/黄/绿，复用统一组件）
    compliance_banner(comp, subtitle=severity_summary(last["dets"]))

    # 手动重播警报（A2 补充：自动播放被浏览器拦截时可用）
    if comp["level"] == "critical" and st.button("🔊 重新播放警报"):
        st.markdown(_alarm_html(), unsafe_allow_html=True)

    col1, col2 = st.columns([3, 2])
    with col1:
        st.image(cv2.cvtColor(annotated, cv2.COLOR_BGR2RGB),
                 caption="实时画面（红框=不合规高亮 / 黄框=警告 / 绿框=合规）",
                 use_column_width=True)
    with col2:
        st.subheader("处置建议")
        for r in comp["reasons"]:
            if r.startswith("【不合规】"):
                st.error(r)
            elif r.startswith("【警告】"):
                st.warning(r)
            else:
                st.success(r)

    with st.expander("本帧检测明细"):
        if last["dets"]:
            for d in last["dets"]:
                st.caption(f"- {d.get('violation_desc', d.get('cls'))} "
                           f"(conf {d.get('conf')}, 场景 {d.get('scene')})")
        else:
            st.caption("（无检测目标）")
