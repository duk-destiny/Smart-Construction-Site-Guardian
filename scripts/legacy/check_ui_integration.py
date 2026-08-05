"""UI 接入模拟测试（Playwright）：本地 mock webhook + 本地视频源。

验证两条接入链路：
1) 外部推送接入：管理端「发送测试推送」-> mock webhook 收到 POST -> 留痕 sent -> UI 提示成功 + 留痕 +1
2) 视频源接入：实时页后台轮询自动运行 -> 轮询次数>0 -> 无异常组件
以用户视角验证「无真实 key」时的接入闭环可走通。
"""
from __future__ import annotations

import os
import re
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


def _exc_count(page) -> int:
    return page.locator('[data-testid="stException"]').count()


def _push_count(body: str):
    m = re.search(r"推送留痕（最近 (\d+) 条）", body)
    return int(m.group(1)) if m else None


def _metric_val(body: str, label: str):
    # Streamlit metric: 标签与值同行附近；尝试抓 label 后的数字
    m = re.search(label + r"\s*([0-9]+|运行中|已停止)", body)
    return m.group(1) if m else None


def main() -> int:
    issues: list[str] = []
    console_errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=CHROMIUM)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda err: issues.append(f"pageerror: {err}"))
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

        # 1) 登录
        page.goto(BASE_URL, wait_until="networkidle", timeout=60000)
        page.wait_for_selector("input", timeout=30000)
        inputs = page.locator("input").all()
        inputs[0].fill("admin")
        inputs[1].fill("admin1234")
        page.locator("button", has_text="登录").first.click()
        page.wait_for_timeout(5000)
        body = page.locator("body").inner_text()
        if "上传与作业票" not in body:
            issues.append("登录后未进入主页")
        page.screenshot(path=str(ROOT / "data/ui_integ_home.png"))

        # 2) 管理端 -> 测试推送
        nav = page.locator('[data-testid="stSidebarNav"]')
        nav.get_by_text("管理端", exact=True).first.click(timeout=15000)
        page.wait_for_timeout(7000)
        body = page.locator("body").inner_text()
        if "外部推送" not in body:
            issues.append("管理端缺少「外部推送」区块")
        if "发送测试推送" not in body:
            issues.append("管理端缺少「发送测试推送」按钮")
        if _exc_count(page):
            issues.append(f"管理端出现 {_exc_count(page)} 个异常组件")
        count_before = _push_count(body)
        page.screenshot(path=str(ROOT / "data/ui_integ_admin_before.png"), full_page=True)

        page.locator("button", has_text="发送测试推送").first.click()
        page.wait_for_timeout(5000)
        body = page.locator("body").inner_text()
        ok_push = "测试推送成功" in body
        if not ok_push:
            issues.append("点击测试推送后未见「测试推送成功」反馈")
        count_after = _push_count(body)
        if count_before is None or count_after is None:
            issues.append(f"推送留痕计数缺失 before={count_before} after={count_after}")
        elif count_after != count_before + 1:
            issues.append(f"推送留痕未 +1（{count_before} -> {count_after}）")
        page.screenshot(path=str(ROOT / "data/ui_integ_admin_push.png"), full_page=True)

        # 3) 实时页 -> 后台轮询面板
        nav.get_by_text("实时摄像头监测", exact=True).first.click(timeout=15000)
        page.wait_for_timeout(6000)
        # 展开后台轮询面板
        page.get_by_text("后台自动轮询监控", exact=True).first.click(timeout=10000)
        page.wait_for_timeout(2000)
        body = page.locator("body").inner_text()
        if "运行状态" not in body or "轮询次数" not in body:
            issues.append("实时页后台轮询面板未渲染指标")
        running = _metric_val(body, "运行状态")
        polls0 = _metric_val(body, "轮询次数")
        if running != "运行中":
            issues.append(f"后台轮询未处于运行中（实际={running}）")
        page.screenshot(path=str(ROOT / "data/ui_integ_realtime_mon.png"), full_page=True)
        # 等待轮询次数增长
        page.wait_for_timeout(12000)
        body2 = page.locator("body").inner_text()
        polls1 = _metric_val(body2, "轮询次数")
        if polls0 is None or polls1 is None:
            issues.append(f"轮询次数读数缺失 {polls0} -> {polls1}")
        elif int(polls1) < int(polls0) + 1:
            issues.append(f"轮询次数未增长 {polls0} -> {polls1}")
        if _exc_count(page):
            issues.append(f"实时页出现 {_exc_count(page)} 个异常组件")
        page.screenshot(path=str(ROOT / "data/ui_integ_realtime_mon2.png"), full_page=True)

        browser.close()

    if console_errors:
        print("CONSOLE_ERRORS:")
        for e in console_errors[:20]:
            print(f"- {e}")
    if issues:
        print("ISSUES:")
        for i in issues:
            print(f"- {i}")
        return 1
    print("INTEGRATION_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())