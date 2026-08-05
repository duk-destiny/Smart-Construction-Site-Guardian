"""UI 操作流测试：登录 → 创建任务 → 多 Agent 研判 → 工单页。"""
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
        page.on("pageerror", lambda err: issues.append(f"pageerror: {err}"))
        page.goto(BASE_URL, wait_until="networkidle", timeout=60000)
        page.wait_for_selector("input", timeout=30000)
        inputs = page.locator("input").all()
        inputs[0].fill("admin")
        inputs[1].fill("admin1234")
        page.locator("button", has_text="登录").click()
        page.wait_for_timeout(4000)

        # 创建任务
        page.locator("button", has_text="开始智能研判").click()
        page.wait_for_timeout(4000)
        body = page.locator("body").inner_text()
        print("=== AFTER CREATE TASK ===")
        print(body[:1200])
        if "多Agent 分步研判" not in body:
            issues.append("创建任务后未跳转到多Agent研判页")

        # 运行多 Agent 研判
        page.locator("button", has_text="运行多Agent研判").click()
        page.wait_for_timeout(15000)
        body = page.locator("body").inner_text()
        print("=== AFTER RUN AGENTS ===")
        print(body[:2500])
        exc = page.locator('[data-testid="stException"]').count()
        if exc:
            issues.append(f"运行多Agent后出现 {exc} 个异常组件")
        page.screenshot(path=str(ROOT / "data/ui_flow_agents.png"), full_page=True)

        # 进入工单页
        nav = page.locator('[data-testid="stSidebarNav"]')
        nav.get_by_text("工单/改判/导出", exact=True).first.click(timeout=15000)
        page.wait_for_timeout(4000)
        body = page.locator("body").inner_text()
        print("=== REPORT PAGE ===")
        print(body[:2000])
        exc = page.locator('[data-testid="stException"]').count()
        if exc:
            issues.append(f"工单页出现 {exc} 个异常组件")
        page.screenshot(path=str(ROOT / "data/ui_flow_report.png"), full_page=True)

        # 人工改判
        reason_input = page.get_by_label("改判原因（必填）")
        if reason_input.count():
            reason_input.fill("UI 自动测试改判")
            page.locator("button", has_text="提交改判").click()
            page.wait_for_timeout(4000)
            body = page.locator("body").inner_text()
            exc = page.locator('[data-testid="stException"]').count()
            print("=== MANUAL OVERRIDE ===")
            print(body[-1200:])
            print("ST_EXCEPTIONS", exc)
            if exc:
                issues.append(f"人工改判出现 {exc} 个异常组件")
            if "改判已记录" not in body:
                issues.append("人工改判未返回成功反馈")
        else:
            issues.append("未找到改判原因输入框")

        # 导出 Excel
        export_btn = page.locator("button", has_text="导出 Excel 台账")
        if export_btn.count():
            export_btn.click()
            page.wait_for_timeout(5000)
            body = page.locator("body").inner_text()
            exc = page.locator('[data-testid="stException"]').count()
            print("=== EXPORT ===")
            print(body[-1200:])
            print("ST_EXCEPTIONS", exc)
            if exc:
                issues.append(f"导出 Excel 出现 {exc} 个异常组件")
            if "已导出" not in body:
                issues.append("导出 Excel 未返回成功反馈")
        else:
            issues.append("未找到导出 Excel 按钮")

        browser.close()

    if issues:
        print("ISSUES:")
        for i in issues:
            print(f"- {i}")
        return 1
    print("UI_FLOW_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
