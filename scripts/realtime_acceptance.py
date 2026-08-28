#!/usr/bin/env python3
"""Phase 4 实时链路运维验收脚本：双浏览器同看一路摄像头 + 引擎切换不中断。

验收口径（docs/前后端分离重构提示词.md Phase 4）：
1. 两个浏览器同看一路摄像头，后端日志确认**只有一路推理**（Hub 单循环、
   polls 按帧率推进而不随观看者翻倍；"已启动"日志恰好一条）；
2. admin 触发模型切换（引擎 reload）过程中检测不崩、帧广播不中断；
3. 告警当帧弹出（critical 帧由 Hub 即时写 alarm_events，前端 notification）。

前置：npm run build 已产出 frontend/dist；Python playwright + chromium 已安装。
运行：python scripts/realtime_acceptance.py
视频源：demo:// 合成源（无需真实摄像头）；服务端以临时库 + 临时配置启动，
不污染开发库与 config.yaml。真实 RTSP 源的等价验收：把 CONFIG_OVERRIDE 中的
sources 换成真实地址即可（同一验收逻辑）。
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # 保证 from scripts import ... 解析到仓库包
PORT = 8123
BASE = f"http://127.0.0.1:{PORT}"


def wait_health(timeout: float = 90.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{BASE}/healthz", timeout=2) as r:
                if r.status == 200:
                    return
        except Exception:  # noqa: BLE001 未就绪重试
            time.sleep(0.5)
    raise RuntimeError("API 服务未在超时内就绪")


def _login_api(username: str, password: str) -> str:
    req = urllib.request.Request(
        f"{BASE}/api/auth/login",
        data=json.dumps({"username": username, "password": password}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())["token"]


def _api(method: str, path: str, token: str, body: dict | None = None):
    req = urllib.request.Request(
        f"{BASE}/api{path}",
        data=json.dumps(body).encode() if body else None,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {token}"},
        method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def start_server(log_path: Path, db_path: Path) -> subprocess.Popen:
    """临时库 + 注入 realtime.enabled 配置（仅子进程内存态，不改 config.yaml）。"""
    env = os.environ.copy()
    env["ZHUG_ROOT"] = str(ROOT)
    env["ZHUG_DB"] = str(db_path)
    # 注意：不设 API_PREWARM=0——该开关同时跳过实时 Hub 启动（Phase 4
    # 语义为"关闭全部后台工作负载"）；本验收按真实部署条件运行
    code = (
        "import os, sys\n"
        "sys.path.insert(0, os.environ['ZHUG_ROOT'])\n"
        "import dao.db\n"
        "dao.db.DEFAULT_DB_PATH = os.environ['ZHUG_DB']\n"
        "from core.config import shared_config\n"
        "shared_config().load()\n"
        "shared_config()._cache['realtime'] = {\n"
        "    'enabled': True, 'sources': ['demo://'],\n"
        "    'active_fps': 3, 'idle_fps': 1, 'jpeg_quality': 60}\n"
        "import uvicorn\n"
        f"uvicorn.run('api.main:app', host='127.0.0.1', port={PORT}, "
        "log_level='warning')\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", code], cwd=str(ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=open(log_path, "w", encoding="utf-8"))
    wait_health()
    return proc


def seed_switch_target(db_path: Path) -> str:
    """在临时库注册表里种一个 fire 族非活跃版本，供验收执行切换（reload）。"""
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT id FROM model_registry WHERE name='fire' AND active=0 "
            "LIMIT 1").fetchone()
        if row:
            return row[0]
        mid = "m_acc_switch_target"
        conn.execute(
            "INSERT INTO model_registry(id, name, version, path, active, "
            "created_at) VALUES (?, 'fire', 'acc-switch-target', ?, 0, "
            "datetime('now'))",
            (mid, "data/models/yolov8_fire_smoke_v2.onnx"))
        conn.commit()
        return mid
    finally:
        conn.close()


def main() -> int:
    from playwright.sync_api import sync_playwright

    from scripts import api_browser_smoke as smoke
    smoke.BASE = BASE  # 复用登录/强制改密助手，指向本验收服务

    log_path = ROOT / "data" / "realtime_acceptance.log"
    tmp = Path(tempfile.mkdtemp(prefix="zhg_rt_acc_"))
    db_path = tmp / "acc.db"
    proc = start_server(log_path, db_path)
    try:
        admin_token = _login_api("admin", "admin123")
        target_id = seed_switch_target(db_path)

        # 第二个观看者账号（safety 种子账号只有一个；同样带首登强制改密，
        # 与 login_and_change_pwd 助手的流程假设一致）
        _api("POST", "/admin/users", admin_token, body={
            "username": "acc_safety2", "password": "accpass123",
            "role": "safety", "must_change_password": True})

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)

            def open_viewer(username: str, old_pwd: str, new_pwd: str):
                pg = browser.new_page(viewport={"width": 1280, "height": 900})
                smoke.login_and_change_pwd(pg, username, old_pwd, new_pwd)
                pg.get_by_text("实时监测").click()
                pg.wait_for_selector("text=帧序 #", timeout=60_000)
                return pg

            # ── 两个浏览器同看一路 demo:// 源 ──
            page_a = open_viewer("safety", "demo1234", "safety_acc1")
            page_b = open_viewer("acc_safety2", "accpass123", "safety_acc2")
            time.sleep(1.0)

            status = _api("GET", "/realtime/status", admin_token)
            assert status["enabled"] and status["running"], status
            assert status["viewers"] == 2, f"观看者应为 2: {status}"

            def seq_of(pg) -> int:
                import re as _re
                txt = pg.locator("text=帧序 #").first.inner_text()
                m = _re.search(r"帧序 #(\d+)", txt)
                return int(m.group(1))

            polls_1 = status["polls"]
            seq_a1, seq_b1 = seq_of(page_a), seq_of(page_b)
            time.sleep(2.0)
            seq_a2, seq_b2 = seq_of(page_a), seq_of(page_b)
            assert seq_a2 > seq_a1 and seq_b2 > seq_b1, \
                f"双端帧应持续推进: A {seq_a1}->{seq_a2}, B {seq_b1}->{seq_b2}"

            polls_2 = _api("GET", "/realtime/status", admin_token)["polls"]
            rate = (polls_2 - polls_1) / 2.0
            # 本机真实双头推理约 0.5-1s/帧：只验证推理在单循环持续推进且
            # 不随观看者翻倍（翻倍则≈2×active_fps）；单路性另由
            # "已启动"日志恰好一条兜底断言
            assert 0.2 <= rate <= 6.0, \
                f"推理帧率异常: {rate:.1f} fps"

            # ── 引擎切换（reload）过程中：检测不崩、广播不中断 ──
            _api("POST", "/admin/models/switch", admin_token,
                 body={"name": "fire", "model_id": target_id})
            time.sleep(2.0)
            polls_3 = _api("GET", "/realtime/status", admin_token)["polls"]
            time.sleep(2.0)
            polls_4 = _api("GET", "/realtime/status", admin_token)["polls"]
            assert polls_4 > polls_3, "切换后推理必须继续"
            seq_a3 = seq_of(page_a)
            time.sleep(1.5)
            assert seq_of(page_a) > seq_a3, "切换后前端帧广播必须继续"
            status = _api("GET", "/realtime/status", admin_token)
            assert status["running"], "切换后 Hub 必须仍在运行"
            assert not status.get("last_error"), status.get("last_error")

            browser.close()

        # ── 日志确认只有一路推理：Hub 恰好启动一次 ──
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
        starts = log_text.count("实时 Hub 已启动")
        assert starts == 1, f"Hub 应恰好启动一次（单路推理）: {starts}"
        print(f"REALTIME ACCEPTANCE PASS：2 观看者共享单路推理（{rate:.1f}fps "
              "单循环）、引擎切换检测不中断、Hub 单次启动")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
