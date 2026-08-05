"""UI 真实用户旅程（Playwright）：登录 -> 实时监控（RTSP/后台轮询面板）-> 管理端（告警生命周期/外部推送/测试推送）。
以用户视角验证新功能页面可操作、无异常组件，并截图留证。"""
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
        if len(inputs) < 2:
            issues.append("登录页输入框不足 2 个")
        else:
            inputs[0].fill("admin")
            inputs[1].fill("admin1234")
        page.locator("button", has_text="登录").first.click()
        page.wait_for_timeout(5000)
        body = page.locator("body").inner_text()
        if "上传与作业票" not in body:
            issues.append("登录后未进入主页")
        if "管理端" not in body:
            issues.append("管理员侧边栏未显示「管理端」入口")
        page.screenshot(path=str(ROOT / "data/ui_journey_home.png"))
        if _exc_count(page):
            issues.append(f"登录后出现 {_exc_count(page)} 个异常组件")

        # 2) 实时页
        nav = page.locator('[data-testid="stSidebarNav"]')
        nav.get_by_text("实时摄像头监测", exact=True).first.click(timeout=15000)
        loaded = False
        for _ in range(40):
            page.wait_for_timeout(1000)
            body = page.locator("body").inner_text()
            if "连续监控" in body or "未加载到任何检测模型" in body:
                loaded = True
                break
        if not loaded:
            issues.append("实时页 40s 内未加载出监控面板")
        body = page.locator("body").inner_text()
        if "实时摄像头监测" not in body:
            issues.append("实时页标题缺失")
        if "未加载到任何检测模型" in body:
            issues.append("实时页提示未加载到检测模型")
        if _exc_count(page):
            issues.append(f"实时页出现 {_exc_count(page)} 个异常组件")
        page.screenshot(path=str(ROOT / "data/ui_journey_realtime.png"), full_page=True)

        # 展开「多路 RTSP / 本地视频源」
        page.get_by_text("多路 RTSP / 本地视频源", exact=True).first.click(timeout=10000)
        page.wait_for_timeout(1500)
        body = page.locator("body").inner_text()
        if "抓取全部源" not in body:
            issues.append("实时页未渲染「抓取全部源」按钮")
        if "每行一个源地址" not in body:
            issues.append("实时页未渲染 RTSP 源输入框")

        # 展开「后台自动轮询监控」
        page.get_by_text("后台自动轮询监控", exact=True).first.click(timeout=10000)
        page.wait_for_timeout(1500)
        body = page.locator("body").inner_text()
        if "后台轮询未启动" not in body:
            issues.append("实时页未显示「后台轮询未启动」提示")
        if "启动后台轮询" not in body:
            issues.append("实时页未渲染「启动后台轮询」按钮")
        page.screenshot(path=str(ROOT / "data/ui_journey_realtime_panels.png"), full_page=True)

        # 3) 管理端
        nav.get_by_text("管理端", exact=True).first.click(timeout=15000)
        page.wait_for_timeout(8000)
        body = page.locator("body").inner_text()
        if _exc_count(page):
            issues.append(f"管理端出现 {_exc_count(page)} 个异常组件")
        for label, text in [
            ("告警生命周期", "告警生命周期"),
            ("告警来源", "来源 camera"),
            ("外部推送", "外部推送"),
            ("未启用提示", "外部推送未启用"),
            ("测试推送按钮", "发送测试推送"),
            ("推送留痕", "推送留痕"),
        ]:
            if text not in body:
                issues.append(f"管理端缺少「{label}：{text}」")
        count_before = _push_count(body)
        page.screenshot(path=str(ROOT / "data/ui_journey_admin.png"), full_page=True)

        # 4) 点击「发送测试推送」（未启用 -> 应返回 skipped 提示并新增一条留痕）
        page.locator("button", has_text="发送测试推送").first.click()
        page.wait_for_timeout(4000)
        body = page.locator("body").inner_text()
        if "测试推送未成功" not in body:
            issues.append("点击测试推送后未见 skipped 反馈")
        count_after = _push_count(body)
        if count_before is None or count_after is None:
            issues.append("推送留痕计数缺失")
        elif count_after != count_before + 1:
            issues.append(f"推送留痕未 +1（{count_before} -> {count_after}）")
        page.screenshot(path=str(ROOT / "data/ui_journey_admin_push.png"), full_page=True)

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
    print("USER_JOURNEY_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())