#!/usr/bin/env python3
"""Phase 3 前端关键流程 Playwright 冒烟：登录→强制改密→文字上报→查结果→
提交整改→验收销项。

前置：npm run build 已产出 frontend/dist；Python playwright + chromium 已安装。
运行：python scripts/api_browser_smoke.py
说明：服务端以临时库启动（uvicorn 子进程，dao.db.DEFAULT_DB_PATH 注入），
不污染开发库；种子账号带初始密码标记，故首登改密流程一并覆盖。
派发环节走 API（派发表单交互由 tests/test_api.py 覆盖），聚焦关键用户链路。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = 8123
BASE = f"http://127.0.0.1:{PORT}"

PNG = (b"\x89PNG\r\n\x1a\n" + b"0" * 64)  # 魔数合法的占位图片


def wait_health(timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{BASE}/healthz", timeout=2) as r:
                if r.status == 200:
                    return
        except Exception:  # noqa: BLE001 未就绪重试
            time.sleep(0.5)
    raise RuntimeError("API 服务未在超时内就绪")


def start_server(db_path: Path) -> subprocess.Popen:
    # 路径经环境变量注入（本机用户目录含撇号，嵌入代码字面量会破坏引号）
    env = os.environ.copy()
    env["ZHUG_ROOT"] = str(ROOT)
    env["ZHUG_DB"] = str(db_path)
    env["API_PREWARM"] = "0"
    code = (
        "import os, sys\n"
        "sys.path.insert(0, os.environ['ZHUG_ROOT'])\n"
        "import dao.db\n"
        "dao.db.DEFAULT_DB_PATH = os.environ['ZHUG_DB']\n"
        "import uvicorn\n"
        f"uvicorn.run('api.main:app', host='127.0.0.1', port={PORT}, "
        "log_level='warning')\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", code], cwd=str(ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    wait_health()
    return proc


def login_and_change_pwd(page, username: str, old_pwd: str, new_pwd: str) -> None:
    page.goto(BASE, wait_until="domcontentloaded")
    # 等待 React 挂载完成再填写：服务端启动期负载高时，fill 早于 hydration
    # 会导致 antd 表单 store 读不到值 → 校验静默失败（运维验收实测）
    page.wait_for_selector("input[placeholder='用户名']", timeout=30_000)
    page.wait_for_timeout(400)
    page.get_by_placeholder("用户名").fill(username)
    page.get_by_placeholder("密码", exact=True).fill(old_pwd)
    page.get_by_role("button", name="登 录").click()
    # 种子账号带初始密码标记 → 强制改密页。
    # 注意：SPA 的 pushState 路由下 Playwright 的 page.url 可能不刷新，
    # 路由断言一律用页面内 location（wait_for_function），不用 wait_for_url。
    try:
        page.wait_for_function("location.pathname.includes('change-password')",
                               timeout=20_000)
    except Exception:
        print(f"[diag] login stuck at {page.url}")
        body = page.inner_text("body")[:300].replace("\n", " | ")
        print("[diag] body:", body)
        print("[diag] token:", page.evaluate("!!localStorage.getItem('zhg_token')"))
        print("[diag] user:", page.evaluate("localStorage.getItem('zhg_user')"))
        raise
    page.get_by_label("原密码").fill(old_pwd)
    page.get_by_label("新密码（至少 8 位）", exact=True).fill(new_pwd)
    page.get_by_label("确认新密码").fill(new_pwd)
    page.get_by_role("button", name="提交修改").click()
    page.wait_for_function("!location.pathname.includes('change-password')",
                           timeout=20_000)


def logout(page) -> None:
    page.get_by_text("（admin）").or_(
        page.get_by_text("（safety）")).or_(
        page.get_by_text("（responsible）")).first.click()
    page.get_by_text("退出登录").click()
    page.wait_for_function("location.pathname.includes('login')",
                           timeout=15_000)


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
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def main() -> int:
    from playwright.sync_api import sync_playwright

    with tempfile.TemporaryDirectory(prefix="zhg_smoke_") as tmp:
        db_path = Path(tmp) / "smoke.db"
        photo = Path(tmp) / "fix.png"
        photo.write_bytes(PNG)
        proc = start_server(db_path)
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1280, "height": 900})
                errors: list[str] = []
                page.on("pageerror",
                        lambda exc: errors.append(f"[pageerror] {exc}"))
                page.on("console", lambda m: errors.append(f"[console] {m.text}")
                        if m.type == "error" else None)

                def diagnose(exc: Exception) -> None:
                    """在 sync_playwright 上下文内调用（连接存活时页面可用）。"""
                    shot = ROOT / "data" / "smoke_failure.png"
                    try:
                        page.screenshot(path=str(shot), timeout=10_000)
                        print(f"截图: {shot}")
                    except Exception as se:  # noqa: BLE001
                        print(f"DIAG screenshot failed: {se}")
                    print(f"FAIL at url={page.url}; "
                          f"exc={type(exc).__name__}: {exc}")
                    try:
                        print("---- 可见文本(截断) ----")
                        print(page.inner_text("body")[:800])
                    except Exception as de:  # noqa: BLE001
                        print(f"DIAG content failed: {de}")
                    for e in errors[-8:]:
                        print("DIAG", e[:300])

                try:
                    # ① safety 登录 + 强制改密
                    login_and_change_pwd(page, "safety", "demo1234",
                                         "safety2026a")
                    assert "/report" in page.url, page.url

                    # ② 文字线索建单（上报）
                    page.get_by_role("tab", name="文字线索建单").click()
                    # 限定激活面板：antd Tabs 非激活面板仍在 DOM（隐藏）
                    pane = page.locator(".ant-tabs-tabpane-active")
                    pane.locator(".ant-select").nth(1).click()
                    page.locator(".ant-select-item-option").first.click()
                    desc = "冒烟测试：3号楼地库配电箱旁堆放易燃纸箱"
                    page.get_by_placeholder(
                        "例：3号楼西侧电焊机旁堆着纸箱没人清理，也没有监火人").fill(desc)
                    page.get_by_role("button", name="创建文字隐患单").click()
                    page.wait_for_selector("text=文字隐患单已创建", timeout=10_000)

                    # ③ 查结果：工单台账出现该隐患
                    page.get_by_text("工单闭环").click()
                    page.wait_for_selector(f"text={desc}", timeout=10_000)

                    # ③' 派发给 lisi（走 API）
                    admin_token = _login_api("admin", "admin123")
                    rows = _api("GET", "/orders", admin_token)
                    task_id = next(r["task_id"] for r in rows
                                   if r["hazard_desc"] == desc)
                    _api("POST", f"/orders/by-task/{task_id}/dispatch",
                         admin_token, body={"assignee": "lisi", "hours": 24})

                    # ④ responsible 登录 → 提交整改（拍照/传图）
                    logout(page)
                    login_and_change_pwd(page, "lisi", "demo1234", "lisi2026a")
                    assert "/my-orders" in page.url, page.url
                    page.get_by_placeholder(
                        "整改说明（必填，如：已清理现场并补充灭火器）").fill(
                        "纸箱已清理，现场恢复并配置灭火器")
                    page.set_input_files("input[type='file']", str(photo))
                    page.get_by_role("button", name="提交整改，等待验收").click()
                    page.wait_for_selector("text=已提交验收", timeout=10_000)

                    # ⑤ admin 登录 → 待验收 → 通过销项
                    logout(page)
                    login_and_change_pwd(page, "admin", "admin123",
                                         "admin2026a")
                    page.get_by_text("工单闭环").click()
                    page.get_by_role("tab", name="待验收").click()
                    # AntD 对两字按钮自动插空格：渲染为「通 过」
                    page.get_by_role("button", name="通 过").first.click()
                    page.get_by_role("button", name="确 定").click()
                    page.wait_for_selector("text=已通过并关闭工单",
                                           timeout=10_000)

                    browser.close()
                except Exception as exc:
                    diagnose(exc)  # 连接仍存活，截图/文本可用
                    raise
            print("SMOKE PASS: 登录→改密→上报→查结果→整改→验收 全链路通过")
            return 0
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    sys.exit(main())
