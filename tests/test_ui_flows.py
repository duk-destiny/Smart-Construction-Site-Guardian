"""UI 用户视角测试：用 Streamlit AppTest 渲染页面并校验关键区块（无需浏览器/服务）。
覆盖外部推送相关界面：
- 管理端：告警生命周期（来源/证据截图）、外部推送（配置展示/测试推送/留痕）；
- 实时页：后台自动轮询监控面板（启动按钮/状态提示）。

说明：dataframe 单元格在 AppTest 里经 pyarrow 反序列化，在全量回归的线程状态下可能死锁，
因此单元格内容改为在包装脚本内直接查库写入 session_state 校验；UI 层仍断言
dataframe 元素存在、留痕计数 caption 等。"""
from __future__ import annotations

import os
import sys
import textwrap

from streamlit.testing.v1 import AppTest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run_script(tmp_path, source, session=None):
    """把包装脚本写入 tmp_path 并用 AppTest 执行，返回 AppTest 实例。"""
    script = tmp_path / "ui_flow_wrapper.py"
    script.write_text(textwrap.dedent(source), encoding="utf-8")
    at = AppTest.from_file(str(script))
    for key, value in (session or {}).items():
        at.session_state[key] = value
    at.run(timeout=120)
    assert not at.exception, [str(e) for e in at.exception]
    return at


def _collect_text(at) -> str:
    parts = []
    for attr in ("title", "header", "subheader", "markdown", "caption",
                 "info", "warning", "error", "success", "metric"):
        for el in getattr(at, attr):
            try:
                parts.append(str(el.value))
            except Exception:  # noqa: BLE001 metric 等可能有嵌套
                parts.append(str(el))
    for btn in at.button:
        parts.append(btn.label)
    return "\n".join(parts)


def test_admin_page_shows_alarm_source_image_and_push_section(tmp_path):
    """管理端渲染：告警显示来源与截图、外部推送配置/测试按钮/留痕列表。"""
    db_file = tmp_path / "hzz_ui.db"
    source = f"""
import os
import sys
sys.path.insert(0, {ROOT!r})
os.chdir({ROOT!r})
import streamlit as st
import ui.page_admin as page
import services.notify_service as ns
from dao.db import get_conn, init_db

_ui_db = {str(db_file)!r}

def _conn(db_path=None):
    conn = get_conn(db_path or _ui_db)
    init_db(conn)
    return conn

# 页面与推送服务使用同一个临时库
page.get_conn = _conn
page.init_db = init_db
ns.get_conn = _conn
ns.init_db = init_db
ns.DEFAULT_DB_PATH = _ui_db   # 测试推送内部 get_conn(db_path or DEFAULT_DB_PATH) 也走临时库

# 预置：告警（带来源与证据截图）、推送留痕
conn = _conn()
from dao.models import AlarmEventDAO, NotificationLogDAO
aid = AlarmEventDAO(conn).insert(
    "s_ui", None, "hot_work", "spark", 0.91,
    image_path="data/ui_login.png", source="rtsp://cam1")
NotificationLogDAO(conn).insert(aid, "wecom", "sent", None)
conn.close()

page.render_admin()

# 记录推送留痕数据（避免 AppTest 对 dataframe 的 pyarrow 反序列化）
conn = _conn()
st.session_state["_db_logs"] = [
    dict(r) for r in conn.execute(
        "SELECT channel, status, error FROM notification_logs ORDER BY id").fetchall()]
conn.close()
"""
    at = _run_script(tmp_path, source, session={
        "role": "admin", "user_id": "u_admin", "username": "admin"})

    text = _collect_text(at)
    assert "告警生命周期" in text
    assert "外部推送" in text
    assert "rtsp://cam1" in text          # 告警来源展示
    assert "来源 camera" in text or "来源" in text
    assert "spark" in text                 # 告警类别
    assert "发送测试推送" in text or any(b.label == "发送测试推送" for b in at.button)
    assert "推送留痕（最近 1 条）" in text   # 留痕计数 caption
    assert len(at.dataframe) >= 1          # 留痕列表已渲染
    assert len(at.image) >= 1              # 证据截图渲染
    assert any(b.label == "发送测试推送" for b in at.button)
    # 留痕数据本身（来自同一临时库，即 UI 列表展示的数据）
    statuses = [r["status"] for r in at.session_state["_db_logs"]]
    assert "sent" in statuses


def test_admin_test_push_button_reports_skipped_when_disabled(tmp_path):
    """管理端点击「发送测试推送」：未启用时给出 skipped 提示并新增留痕，不抛异常。"""
    db_file = tmp_path / "hzz_ui2.db"
    source = f"""
import os
import sys
sys.path.insert(0, {ROOT!r})
os.chdir({ROOT!r})
import streamlit as st
import ui.page_admin as page
import services.notify_service as ns
from dao.db import get_conn, init_db

_ui_db = {str(db_file)!r}

def _conn(db_path=None):
    conn = get_conn(db_path or _ui_db)
    init_db(conn)
    return conn

page.get_conn = _conn
page.init_db = init_db
ns.get_conn = _conn
ns.init_db = init_db
ns.DEFAULT_DB_PATH = _ui_db   # 测试推送内部 get_conn(db_path or DEFAULT_DB_PATH) 也走临时库

page.render_admin()

conn = _conn()
st.session_state["_db_logs"] = [
    dict(r) for r in conn.execute(
        "SELECT channel, status, error FROM notification_logs ORDER BY id").fetchall()]
conn.close()
"""
    at = _run_script(tmp_path, source, session={
        "role": "admin", "user_id": "u_admin", "username": "admin"})

    # 未启用推送 → info 提示 + 0 条留痕
    text = _collect_text(at)
    assert "外部推送未启用" in text
    assert "推送留痕（最近 0 条）" in text

    # 点击测试推送按钮
    btn = next((b for b in at.button if b.label == "发送测试推送"), None)
    assert btn is not None
    btn.click()
    at.run(timeout=120)
    assert not at.exception, [str(e) for e in at.exception]
    after = _collect_text(at)
    assert "测试推送未成功" in after
    assert "skipped" in after
    assert "推送留痕（最近 1 条）" in after   # 留痕 +1
    statuses = [r["status"] for r in at.session_state["_db_logs"]]
    assert "skipped" in statuses


def test_realtime_page_shows_monitor_panel(tmp_path):
    """实时页渲染：后台自动轮询面板与 RTSP 抓取入口可见。"""
    source = f"""
import os
import sys
sys.path.insert(0, {ROOT!r})
os.chdir({ROOT!r})
import ui.page_realtime as page
page.render_realtime()
"""
    at = _run_script(tmp_path, source, session={"role": "safety"})

    text = _collect_text(at)
    assert "实时摄像头监测" in text
    assert "后台轮询未启动" in text
    assert any(b.label == "启动后台轮询" for b in at.button)
    assert any(b.label == "抓取全部源" for b in at.button)

def test_admin_model_version_dropdown_switches_and_syncs_config(tmp_path):
    """管理端「模型版本与回滚」下拉框：选历史版本→一键切换→回写 config + DB 翻转。"""
    import shutil
    import sqlite3
    db_file = tmp_path / "hzz_model.db"
    cfg_copy = tmp_path / "config_copy.yaml"
    # 复制真实 config 作为隔离写入目标（_sync_config_path 的正则才能命中 v2→v3）
    shutil.copy2(os.path.join(ROOT, "config", "config.yaml"), cfg_copy)

    source = f"""
import os, sys
sys.path.insert(0, {ROOT!r})
os.chdir({ROOT!r})
import streamlit as st
import ui.page_admin as page
import services.model_service as _ms
from dao.db import get_conn, init_db
from services.model_service import ModelService

_ui_db = {str(db_file)!r}
_cfg_copy = {str(cfg_copy)!r}

# 隔离 config 写入：_sync_config_path 读/写都落到临时副本，不碰真实 config
class _CL:
    def __init__(self, path=None):
        self._path = _cfg_copy
_ms.ConfigLoader = _CL

def _conn(db_path=None):
    conn = get_conn(db_path or _ui_db)
    init_db(conn)
    return conn

page.get_conn = _conn
page.init_db = init_db
page._reload_running_engines = lambda: None  # 避免在 AppTest 进程加载 ONNX/torch

# 预置两个 fire 版本：v2 活跃、v3 备选
# 仅在首次注册：AppTest 每次 at.run() 都会重跑整段脚本，不守门会产生重复行
conn = _conn()
_ms_local = ModelService(conn)
if not [m for m in _ms_local.list_models()
        if m["name"] == "fire" and m["version"] == "v3"]:
    _ms_local.register(
        name="fire", version="v2", path="data/models/yolov8_fire_smoke_v2.onnx",
        mAP50=0.80, mAP50_95=0.55, active=True)
    _ms_local.register(
        name="fire", version="v3", path="data/models/yolov8_fire_smoke_v3.onnx",
        mAP50=0.85, mAP50_95=0.60, active=False)
conn.close()

page.render_admin()
"""
    at = _run_script(tmp_path, source, session={
        "role": "admin", "user_id": "u_admin", "username": "admin"})

    # 1) 下拉框渲染，默认选中当前活跃版本 v2
    sb = next((s for s in at.selectbox if s.key == "model_ver_sel_fire"), None)
    assert sb is not None, "fire 版本下拉框未渲染"
    assert sb.value == "v2", f"默认应选中活跃 v2，实际 {sb.value!r}"

    # 2) 选 v3（非活跃）→ 出现「一键切换」按钮
    sb.select("v3")
    at.run(timeout=120)
    assert not at.exception, [str(e) for e in at.exception]
    btn = next((b for b in at.button
                if "一键切换" in b.label and "fire" in b.label and "v3" in b.label), None)
    assert btn is not None, "选中非活跃版本后未出现一键切换按钮"

    # 3) 点击切换 → 临时 config 同步成 v3 + DB 翻转
    btn.click()
    at.run(timeout=120)
    assert not at.exception, [str(e) for e in at.exception]

    cfg_text = cfg_copy.read_text(encoding="utf-8")
    assert "data/models/yolov8_fire_smoke_v3.onnx" in cfg_text, "config 未回写为 v3"
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    # 用 get_active 语义（active=1 + 最新）断言，避免重复行下 fetchone 命中旧行
    active_fire = conn.execute(
        "SELECT version, path FROM model_registry WHERE name='fire' AND active=1 "
        "ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    assert active_fire and active_fire["version"] == "v3", "DB: 活跃 fire 未切到 v3"
    assert active_fire["path"].endswith("yolov8_fire_smoke_v3.onnx"), "DB: 活跃路径非 v3"
    conn.close()
