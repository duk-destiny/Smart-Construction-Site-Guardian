"""页面：系统自检（page_diag，仅 admin）。

一键自检整条接入链路：模型加载 → 视频源连通 → webhook/回环可达 → DB 读写 → 假告警→推送全链路
→ AI 通道连通（v0.8：云 LLM / 云 ASR 仅在已配置时渲染检查行，本地 Ollama 常驻检查）。
无真实 key 时开启管理端「演示模式」即可走通回环，无需外部 mock 进程或改 config。

改进点：
- 逐项进度（st.status）：每跑完一项即时亮灯，运维能快速定位卡在哪一环；
- data-testid 标记：每项结果行 + 总结带稳定锚点，Playwright/自检脚本精确定位；
- safe_page 错误降级：render 抛异常时降级为可读提示，不冒泡成 stException。
"""
from __future__ import annotations

import re

import streamlit as st

from dao.db import DEFAULT_DB_PATH, get_conn, init_db
from services.model_service import ModelService
from services.notify_service import NotificationService
from services.task_service import TaskService
from ui.page_helpers import diag_row, safe_page


def _ok(flag: bool) -> str:
    return "✅" if flag else "❌"


def _check_models() -> tuple[bool, str]:
    try:
        ms = ModelService(get_conn())
        names = []
        for name in ("fire", "ppe"):
            m = ms.active_model(name)
            if m:
                row = dict(m) if not isinstance(m, dict) else m
                names.append(f"{name}={row.get('version') or row.get('name') or '已加载'}")
            else:
                names.append(f"{name}=未注册")
        return True, "；".join(names)
    except Exception as exc:  # noqa: BLE001
        return False, f"加载异常：{exc}"[:120]


def _check_sources(sources: list[str]) -> tuple[bool, str]:
    from core.video_source import check_source
    srcs = [s for s in (sources or []) if s and str(s).strip()] or ["demo://"]
    lines = []
    all_ok = True
    for src in srcs:
        r = check_source(src)
        if not r["ok"]:
            all_ok = False
        lines.append(
            f"{src} -> {_ok(r['ok'])} {r['width']}x{r['height']} {r['fps']:.1f}fps"
            + (f" ｜ {r['error']}" if r["error"] else "")
        )
    return all_ok, "；".join(lines)


def _check_webhook(notify_svc: NotificationService) -> tuple[bool, str]:
    if notify_svc._demo_mode():
        return True, "演示模式（回环，不发真实 HTTP）"
    url = notify_svc.webhook_url()
    if not url:
        return False, "未配置 webhook_url（可开启演示模式跳过）"
    if not notify_svc.enabled():
        return False, "notify.enabled=false"
    res = notify_svc.test_push()
    return res.get("ok", False), f"测试推送 {res.get('status')} ｜ {res.get('error') or ''}"


def _check_db() -> tuple[bool, str]:
    try:
        conn = get_conn()
        init_db(conn)
        counts = {}
        for t in ("alarm_events", "notification_logs", "task_records"):
            try:
                counts[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except Exception:  # noqa: BLE001
                counts[t] = "—"
        return True, f"{DEFAULT_DB_PATH} ｜ " + " ｜ ".join(f"{k}={v}" for k, v in counts.items())
    except Exception as exc:  # noqa: BLE001
        return False, f"DB 异常：{exc}"[:120]


def _check_fulllink(notify_svc: NotificationService) -> tuple[bool, str]:
    try:
        conn = get_conn()
        init_db(conn)
        ts = TaskService(conn)
        aid = ts.create_alarm_event(
            session_id="selftest", task_id=None, scene_id="hot_work",
            cls="spark", conf=0.99, source="自检", force=True)
        if not aid:
            return False, "创建告警事件失败"
        res = notify_svc.push_alarm(aid)
        if res.get("ok"):
            tag = "（模拟）" if notify_svc._demo_mode() else ""
            return True, f"告警 {aid} -> 推送 {res.get('status')}{tag}"
        return False, f"告警 {aid} -> 推送 {res.get('status')} ｜ {res.get('error')}"
    except Exception as exc:  # noqa: BLE001
        return False, f"全链路异常：{exc}"[:120]


# ---------- v0.8 AI 通道连通性（可选增强，主链路零依赖）----------

def _ai_channels() -> dict:
    """探测 AI 通道配置——云 provider 列表（每家一行）与云 ASR 是否已配置。"""
    out: dict = {"llm_cloud": [], "asr": False}
    try:
        from services.enhance_service import EnhanceEngine
        out["llm_cloud"] = [p["name"] for p in EnhanceEngine().providers
                            if p["type"] == "cloud"]
    except Exception:  # noqa: BLE001
        pass
    try:
        from core.asr_engine import AsrEngine
        out["asr"] = AsrEngine().available()
    except Exception:  # noqa: BLE001
        pass
    return out


def _make_llm_provider_check(name: str):
    """生成单个云 provider 的连通性检查函数（自检清单行闭包）。"""
    def _fn() -> tuple[bool, str]:
        from services.enhance_service import EnhanceEngine
        eng = EnhanceEngine()
        p = next((x for x in eng.providers
                  if x["type"] == "cloud" and x["name"] == name), None)
        if p is None:
            return True, "未配置"
        r = eng.check_provider(p)
        return r["ok"], f"{r['detail']}（{r['cost_ms']}ms）"
    return _fn


def _check_asr_cloud() -> tuple[bool, str]:
    from core.asr_engine import AsrEngine
    eng = AsrEngine()
    if not eng.available():
        return True, "未配置"
    r = eng.check_connectivity()
    return r["ok"], f"{r['detail']}（{r['cost_ms']}ms）"


def _check_ollama() -> tuple[bool, str]:
    try:
        from core.llm_engine import LlmEngine
        eng = LlmEngine()
    except Exception as exc:  # noqa: BLE001
        return False, f"配置读取失败：{exc}"[:120]
    if not eng._enabled:
        return True, "已关闭（llm.enabled=false，润色走模板）"
    if eng.available():
        return True, f"{eng.model} 可调用"
    return False, "Ollama/模型不可达（润色将降级模板，不影响研判主链路）"


# 自检清单：(key, 标签, 检查函数) —— key 用于 data-testid 锚点
def _build_checks(sources, notify_svc,
                  ai_channels: dict | None = None):
    if ai_channels is None:
        ai_channels = _ai_channels()
    checks = [
        ("models", "模型加载", _check_models),
        ("sources", "视频源连通", lambda: _check_sources(sources)),
        ("webhook", "webhook/回环可达", lambda: _check_webhook(notify_svc)),
        ("db", "DB 读写", _check_db),
        ("fulllink", "假告警→推送全链路", lambda: _check_fulllink(notify_svc)),
    ]
    # AI 通道（可选增强）：每个云 provider 一行（v0.8 多 base 接入）；
    # 未配置的通道整行不渲染（静默约定）；本地 Ollama 常驻检查
    for _name in ai_channels.get("llm_cloud") or []:
        _safe = re.sub(r"[^\w\-]+", "_", str(_name)) or "cloud"
        checks.append((f"llm_{_safe}", f"云 LLM·{_name}",
                       _make_llm_provider_check(_name)))
    if ai_channels.get("asr"):
        checks.append(("asr_cloud", "云 ASR 通道", _check_asr_cloud))
    checks.append(("llm_local", "本地 Ollama", _check_ollama))
    return checks


@safe_page("系统自检")
def render_diag() -> None:
    st.title("🩺 系统自检")
    if st.session_state.get("role") != "admin":
        st.error("无权限访问系统自检")
        return

    st.caption("一键自检整条接入链路。无真实 webhook 时可到管理端开启「演示模式」后再跑。")
    demo = bool(st.session_state.get("notify_demo", False))
    notify_svc = NotificationService(demo_mode=demo)
    st.caption(f"当前推送模式：{'演示（回环）' if notify_svc._demo_mode() else '真实通道'}")

    # v0.8：AI 通道配置状态一眼可见（未配置的云通道自动跳过检查，不报红）
    ai = _ai_channels()
    _llm_txt = ("、".join(ai.get("llm_cloud") or []) + "（各一行检查）"
                if ai.get("llm_cloud") else "未配置（跳过）")
    st.caption("AI 通道（可选增强，主链路零依赖）："
               f"云 LLM {_llm_txt} ｜ "
               f"云 ASR {'已配置 ✅将检查' if ai.get('asr') else '未配置（跳过）'} ｜ "
               "本地 Ollama 始终检查")

    sources: list[str] = []
    try:
        from core.config import ConfigLoader
        mconf = ConfigLoader().get("monitor") or {}
        sources = [str(x).strip() for x in (mconf.get("sources") or []) if str(x).strip()]
    except Exception:  # noqa: BLE001
        sources = []

    if st.button("一键自检", type="primary", key="diag_run"):
        checks = _build_checks(sources, notify_svc)
        all_ok = True
        with st.status("自检中…", expanded=True) as status:
            for key, label, fn in checks:
                st.write(f"检查 {label}…")
                ok, detail = fn()
                if not ok:
                    all_ok = False
                diag_row(label, ok, detail, key)
            status.update(
                label="全部自检通过 ✅" if all_ok else "部分项未通过 ❗",
                state="complete" if all_ok else "error",
                expanded=True,
            )
        st.divider()
        # 总结锚点（隐藏，供自检脚本定位）+ 可读提示
        tag = "pass" if all_ok else "fail"
        st.markdown(
            f'<div data-testid="diag-summary" style="display:none">{tag}</div>',
            unsafe_allow_html=True,
        )
        if all_ok:
            st.success("全部自检通过 ✅")
        else:
            st.warning("部分项未通过，详见上方红项")