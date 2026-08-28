"""页面：实时摄像头监测（A1/A2/A3/B1/B2）。

零依赖轮询方案（A）：st.camera_input 捕获帧 → 轻链路检测 → 三级合规 + 红框高亮；
不合规时触发 800Hz 声音警报（仅实时态，A2）与 Toast 提示（B2）。
Phase 0：引擎单例/视频源工具在 services.realtime_entry，帧持久化与告警
链路在 services.history_service——本页零 get_conn/DAO/core。
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
from ui.page_helpers import safe_page

from services import history_service, realtime_entry
from ui.components import compliance_banner, severity_summary
from core.logging import get_logger
log = get_logger(__name__)


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


# 引擎单例/预热已收口到 services.realtime_entry（Phase 0）：
# 预热线程（app._background_prewarm）与页面经同一入口共享实例；
# admin 换模型后 page_admin._reload_running_engines 经该入口调 reload()。


def _persist(session_id: str, frame_status: str, dets: list[dict]) -> None:
    """单帧持久化（连接与 SQL 在 services.history_service；失败留痕不中断）。"""
    history_service.record_frame(session_id, frame_status, dets, mode="realtime")


@safe_page("实时摄像头监测")
def render_realtime() -> None:
    st.title("📷 实时摄像头监测")

    if "_realtime_session" not in st.session_state:
        st.session_state["_realtime_session"] = f"s_{uuid.uuid4().hex[:12]}"

    engine = realtime_entry.get_engine()
    if not engine.available:
        st.error("未加载到任何检测模型，请确认 data/models 下权重文件存在（"
                 "yolov8_fire_smoke_v2.onnx / ppe_yolov8_v2.onnx）。")
        return

    st.caption("动火作业安全 + 施工 PPE 双场景同时接入（复用现有检测头）。"
               "不合规时自动播放 800Hz 声音警报并弹窗提醒。")
    continuous = st.toggle("连续监控（捕获后自动刷新下一帧）", value=True)
    alarm_cooldown = st.slider("告警冷却（秒）", 1, 30, 5,
                               key="alarm_cooldown", help="同一帧/短时间重复告警前的最小间隔")

    with st.expander("多路 RTSP / 本地视频源"):
        sources_text = st.text_area(
            "每行一个源地址",
            key="rtsp_sources",
            placeholder="rtsp://user:pass@host:554/stream\nD:/videos/cam1.mp4",
        )
        if st.button("抓取全部源", type="primary", key="rtsp_grab"):
            sources = [line.strip() for line in sources_text.splitlines() if line.strip()]
            if not sources:
                st.warning("请先输入至少一个 RTSP/本地视频源")
            else:
                results = realtime_entry.MultiSourceMonitor(sources).grab_all(engine.analyze, engine.draw)
                st.session_state["_rtsp_results"] = results
                for r in results:
                    if not r.get("ok"):
                        continue
                    comp_r = r["compliance"]
                    dets_r = r["detections"] or []
                    _persist(st.session_state["_realtime_session"],
                             comp_r["status"], dets_r)
                    if comp_r.get("level") == "critical" and dets_r:
                        try:
                            # Phase 0：高危项选取（severity 查表）与告警
                            # 链路（建告警→证据→推送→条款挂载）全在服务层
                            history_service.raise_critical_alarm(
                                session_id=st.session_state.get("_realtime_session"),
                                dets=dets_r,
                                source=r.get("source"),
                                annotated_bgr=r.get("annotated"),
                            )
                        except Exception as exc:  # noqa: BLE001 单条告警失败不中断本轮
                            log.warning(f"实时告警触发失败（{r.get('source')}）: {exc}")
                st.success(f"已抓取 {sum(1 for r in results if r.get('ok'))} 路源")
    with st.expander("后台自动轮询监控"):
        import services.monitor_service as mon_svc
        mon = mon_svc.get_monitor()
        if mon is None:
            st.caption("后台轮询未启动：config.yaml 中 monitor.enabled=false 或未配置 sources。")
            if st.button("启动后台轮询", key="mon_start"):
                from core.config import shared_config
                mconf = shared_config().get("monitor") or {}
                msrcs = [str(x).strip() for x in (mconf.get("sources") or []) if str(x).strip()]
                if not msrcs:
                    st.warning("config.yaml 的 monitor.sources 为空，无法启动")
                else:
                    mon_svc.start_monitor(
                        msrcs,
                        interval_sec=float(mconf.get("interval_sec", 10) or 10),
                        cooldown_sec=float(mconf.get("cooldown_sec", 60) or 60))
                    st.success("后台轮询已启动")
                    st.rerun()
        else:
            mstatus = mon.status()
            m1, m2, m3 = st.columns(3)
            m1.metric("运行状态", "运行中" if mstatus["running"] else "已停止")
            m2.metric("轮询次数", mstatus["polls"])
            m3.metric("产生告警", mstatus["alarms"])
            st.caption(f"间隔 {mstatus['interval_sec']:.0f}s ｜ 冷却 {mstatus['cooldown_sec']:.0f}s ｜ 源数 {len(mstatus['sources'])}")
            if mstatus["sources"]:
                # v0.8：展示层打码 RTSP 凭据（数据库/内部链路仍存原始 source）
                st.code("\n".join(realtime_entry.mask_source(s) for s in mstatus["sources"]))
            if mstatus["last_error"]:
                st.error(mstatus["last_error"])
            if st.button("停止后台轮询", key="mon_stop"):
                mon_svc.stop_monitor()
                st.success("后台轮询已停止")
                st.rerun()
            st.divider()
            if st.button("源连通性自检", key="mon_src_check"):
                for _src in (mstatus["sources"] or ["demo://"]):
                    _r = realtime_entry.check_source(_src)
                    _flag = "✅" if _r["ok"] else "❌"
                    st.caption(f"{_flag} {realtime_entry.mask_source(_src)} ｜ {_r['width']}×{_r['height']} ｜ {_r['fps']:.1f}fps" + (f" ｜ {_r['error']}" if _r['error'] else ""))
    _show_rtsp_results()

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

    # 告警生命周期：高危帧创建告警事件 → 证据截图留存 → 异步外部推送
    # （高危项选取 = severity 查表，属合规业务判定，收口在服务层）
    if comp["level"] == "critical" and dets:
        try:
            history_service.raise_critical_alarm(
                session_id=st.session_state.get("_realtime_session"),
                dets=dets,
                source="camera",
                annotated_bgr=annotated,
            )
        except Exception as exc:  # noqa: BLE001 告警失败不中断监测，但留痕
            log.warning(f"实时告警触发失败（camera）: {exc}")

    # 不合规：声音警报（A2）+ Toast（B2）
    now = time.time()
    if now - st.session_state.get("_alarm_last", 0) >= alarm_cooldown:
        if comp["level"] == "critical":
            st.toast("⚠️ 检测到高危违规，请立即处置！", icon="⚠️")
            st.markdown(_alarm_html(), unsafe_allow_html=True)
        elif comp["level"] == "warning":
            st.toast("⚠️ 发现需关注项，请尽快整改。", icon="⚠️")
        st.session_state["_alarm_last"] = now

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
                 use_container_width=True)
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
                           f"(conf {d.get('conf')}, 场景 {d.get('scene')}, "
                           f"track #{d.get('track_id') or '—'}, "
                           f"连续 {d.get('track_frames') or 1} 帧)")
        else:
            st.caption("（无检测目标）")


def _show_rtsp_results() -> None:
    """展示最近一次多路 RTSP/本地视频源抓取结果。"""
    results = st.session_state.get("_rtsp_results") or []
    if not results:
        return
    st.divider()
    st.subheader("多路视频源结果")
    for r in results:
        st.caption(f"源 {r['index'] + 1}：{realtime_entry.mask_source(str(r['source']))}")
        if not r.get("ok"):
            st.warning("读取失败，请检查 RTSP/文件路径后重试")
            continue
        comp = r["compliance"]
        compliance_banner(comp, subtitle=severity_summary(r["detections"]))
        col1, col2 = st.columns([3, 2])
        with col1:
            st.image(cv2.cvtColor(r["annotated"], cv2.COLOR_BGR2RGB),
                     caption="远程/文件画面（红框=不合规 / 黄框=警告 / 绿框=合规）",
                     use_container_width=True)
        with col2:
            st.subheader("处置建议")
            for reason in comp["reasons"]:
                if reason.startswith("【不合规】"):
                    st.error(reason)
                elif reason.startswith("【警告】"):
                    st.warning(reason)
                else:
                    st.success(reason)
