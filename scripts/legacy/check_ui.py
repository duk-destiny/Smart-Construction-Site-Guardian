"""Streamlit UI 冒烟检查：登录页、控制台错误、异常组件、截图。"""
from __future__ import annotations

import sys
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
BASE_URL = "http://127.0.0.1:8501"
CHROMIUM = os.environ.get(
    "PLAYWRIGHT_CHROMIUM",
    r"C:\Users\k'k\AppData\Local\ms-playwright\chromium-1228\chrome-win64\chrome.exe",
)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def main() -> int:
    issues: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=CHROMIUM)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        console_errors: list[str] = []

        def on_console(msg):
            if msg.type in ("error", "warning"):
                console_errors.append(f"{msg.type}: {msg.text}")

        def on_page_error(err):
            console_errors.append(f"pageerror: {err}")

        page.on("console", on_console)
        page.on("pageerror", on_page_error)
        page.goto(BASE_URL, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(1500)
        page.screenshot(path=str(ROOT / "data/ui_login.png"), full_page=True)
        login_text = page.locator("body").inner_text()
        print("=== LOGIN PAGE TEXT ===")
        print(login_text[:1200])
        print("=== LOGIN BUTTONS ===")
        print([b.inner_text() for b in page.locator("button").all()])

        # 尝试用演示管理员账号登录
        inputs = page.locator("input").all()
        if len(inputs) >= 2:
            inputs[0].fill("admin")
            inputs[1].fill("admin1234")
            page.locator("button", has_text="登录").click()
            page.wait_for_timeout(4000)
            page.screenshot(path=str(ROOT / "data/ui_upload.png"), full_page=True)
            body = page.locator("body").inner_text()
            print("=== AFTER LOGIN TEXT ===")
            print(body[:2000])
        else:
            issues.append("登录页未找到用户名/密码输入框")

        for label, slug in [
            ("实时摄像头监测", "realtime"),
            ("多Agent研判", "agents"),
            ("工单/改判/导出", "report"),
            ("检测历史与分析", "history"),
            ("管理端", "admin"),
        ]:
            try:
                nav = page.locator('[data-testid="stSidebarNav"]')
                nav.get_by_text(label, exact=True).first.click(timeout=15000)
                page.wait_for_timeout(3000)
                page.screenshot(
                    path=str(ROOT / f"data/ui_{slug}.png"), full_page=True)
                exceptions = page.locator('[data-testid="stException"]').count()
                body = page.locator("body").inner_text()
                print(f"=== PAGE {label} ===")
                print(body[:1500])
                print(f"ST_EXCEPTIONS={exceptions}")
                if exceptions:
                    issues.append(f"{label} 页面出现 {exceptions} 个 Streamlit 异常组件")
            except Exception as e:
                issues.append(f"{label} 页面点击/渲染失败: {type(e).__name__}: {e}")

        exceptions = page.locator('[data-testid="stException"]').count()
        print("=== FINAL ST EXCEPTIONS ===", exceptions)
        if exceptions:
            issues.append(f"页面出现 {exceptions} 个 Streamlit 异常组件")
        print("=== CONSOLE ISSUES ===")
        for e in console_errors[:20]:
            print(e)
        browser.close()

    if issues:
        print("ISSUES:")
        for i in issues:
            print(f"- {i}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
