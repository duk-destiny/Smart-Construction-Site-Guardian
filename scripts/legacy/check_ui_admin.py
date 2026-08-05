"""UI 管理端功能检查：PDF 解析入库。"""
from __future__ import annotations

import glob
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
    pdf = glob.glob(str(ROOT / "data/kb/*.pdf"))[0]
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=CHROMIUM)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda err: issues.append(f"pageerror: {err}"))
        page.goto(BASE_URL, wait_until="networkidle", timeout=60000)
        page.wait_for_selector("input", timeout=30000)
        inputs = page.locator("input").all()
        inputs[0].fill("admin")
        inputs[1].fill("admin1234")
        page.locator("button", has_text="登录").click()
        page.wait_for_timeout(4000)

        nav = page.locator('[data-testid="stSidebarNav"]')
        nav.get_by_text("管理端", exact=True).first.click(timeout=15000)
        page.wait_for_timeout(3000)

        page.locator('input[type="file"]').first.set_input_files(pdf)
        page.locator("button", has_text="解析入库").click()
        page.wait_for_timeout(20000)
        body = page.locator("body").inner_text()
        print("=== ADMIN IMPORT RESULT ===")
        print(body[-2500:])
        exc = page.locator('[data-testid="stException"]').count()
        print("ST_EXCEPTIONS", exc)
        if exc:
            issues.append(f"解析入库页面出现 {exc} 个异常组件")
        if "入库成功" not in body and "解析失败" not in body:
            issues.append("未看到导入成功或失败反馈")
        page.screenshot(path=str(ROOT / "data/ui_admin_import.png"), full_page=True)
        browser.close()

    if issues:
        print("ISSUES:")
        for i in issues:
            print(f"- {i}")
        return 1
    print("ADMIN_IMPORT_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
