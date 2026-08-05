"""RBAC UI 检查：安全员不应看到或进入管理端。"""
from __future__ import annotations

import os
import sys
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
        page.goto(BASE_URL, wait_until="networkidle", timeout=60000)
        page.wait_for_selector("input", timeout=30000)
        inputs = page.locator("input").all()
        inputs[0].fill("safety")
        inputs[1].fill("demo1234")
        page.locator("button", has_text="登录").click()
        page.wait_for_timeout(4000)
        body = page.locator("body").inner_text()
        print("=== SAFETY NAV ===")
        print(body[:1200])
        if "管理端" in body:
            issues.append("安全员登录后看到了管理端导航")

        page.goto(f"{BASE_URL}/render_admin", wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(3000)
        body = page.locator("body").inner_text()
        exc = page.locator('[data-testid="stException"]').count()
        print("=== SAFETY DIRECT ADMIN ===")
        print(body[:1500])
        print("ST_EXCEPTIONS", exc)
        if "管理端（仅管理员）" in body and "无权限" not in body:
            issues.append("安全员直接访问管理端 URL 后进入了管理功能")
        browser.close()

    if issues:
        print("ISSUES:")
        for i in issues:
            print(f"- {i}")
        return 1
    print("RBAC_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
