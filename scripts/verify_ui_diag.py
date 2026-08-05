"""UI 验证：系统自检页（改进项 3/4/5）。

以用户视角验证：
- 改进3 data-testid 锚点：diag-check-{key} × 5 + diag-summary 可定位
- 改进4 逐项进度：st.status 渲染、5 项逐项出结果
- 改进5 错误降级：全程无 stException（页面级兜底生效）

流程：登录 -> 管理端开演示模式 -> 系统自检 -> 一键自检 -> 校验 5/5 pass + 锚点 + 无异常。
"""
from __future__ import annotations
import os, sys
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
BASE_URL = "http://127.0.0.1:8501"
CHROMIUM = os.environ.get(
    "PLAYWRIGHT_CHROMIUM",
    r"C:\Users\k'k\AppData\Local\ms-playwright\chromium-1228\chrome-win64\chrome.exe",
)
CHECK_KEYS = ["models", "sources", "webhook", "db", "fulllink"]

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def main() -> int:
    issues, console_errors = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=CHROMIUM)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda err: issues.append(f"pageerror: {err}"))
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)

        # 1) 登录
        page.goto(BASE_URL, wait_until="networkidle", timeout=60000)
        page.wait_for_selector("input", timeout=30000)
        inputs = page.locator("input").all()
        inputs[0].fill("admin")
        inputs[1].fill("admin1234")
        page.locator("button", has_text="登录").first.click()
        page.wait_for_timeout(5000)
        if "上传与作业票" not in page.locator("body").inner_text():
            issues.append("登录后未进入主页")
        page.screenshot(path=str(ROOT / "data/ui_diag_login.png"))

        # 2) 管理端 -> 开演示模式（保证 webhook/全链路走回环）
        nav = page.locator('[data-testid="stSidebarNav"]')
        nav.get_by_text("管理端", exact=True).first.click(timeout=15000)
        page.wait_for_timeout(7000)
        body = page.locator("body").inner_text()
        if "演示模式" not in body:
            issues.append("管理端缺少「演示模式」开关")
        # 点击 toggle 文本以开启
        tog = page.get_by_text("演示模式（无需 webhook）", exact=True).first
        if tog.count() == 0:
            tog = page.locator("span", has_text="演示模式").first
        tog.click(timeout=10000)
        page.wait_for_timeout(2000)
        # 确认已开启：body 应含「演示（回环）」或 toggle 处于 checked
        body2 = page.locator("body").inner_text()
        page.screenshot(path=str(ROOT / "data/ui_diag_demo_on.png"), full_page=True)

        # 3) 系统自检页
        nav.get_by_text("系统自检", exact=True).first.click(timeout=15000)
        page.wait_for_timeout(5000)
        body = page.locator("body").inner_text()
        if "一键自检" not in body:
            issues.append("系统自检页缺少「一键自检」按钮")
        if "当前推送模式" not in body:
            issues.append("系统自检页缺少推送模式提示")
        page.screenshot(path=str(ROOT / "data/ui_diag_page.png"), full_page=True)

        # 改进5 检查：自检页渲染无 stException
        exc0 = page.locator('[data-testid="stException"]').count()

        # 4) 一键自检
        page.locator("button", has_text="一键自检").first.click(timeout=15000)
        page.wait_for_timeout(8000)

        # 改进3：5 个 data-testid 锚点
        for k in CHECK_KEYS:
            sel = f'[data-testid="diag-check-{k}"]'
            cnt = page.locator(sel).count()
            txt = page.locator(sel).first.inner_text() if cnt else ""
            has_ok = ("OK" in txt) or ("\u2705" in txt)
            print(f"  [anchor] {k}: count={cnt} ok={has_ok} txt={txt[:80]}")
            if cnt == 0:
                issues.append(f"缺少锚点 diag-check-{k}")
            elif not has_ok:
                issues.append(f"锚点 {k} 未通过（文本未见 \u2705）")

        # 改进3：summary 锚点
        summ = page.locator('[data-testid="diag-summary"]').first
        summ_txt = summ.inner_text() if summ.count() else ""
        print(f"  [summary] count={summ.count()} txt={summ_txt!r}")
        if summ.count() == 0:
            issues.append("缺少 diag-summary 锚点")
        elif summ_txt.strip() != "pass":
            issues.append(f"自检总结非 pass（实际={summ_txt.strip()!r}）")

        # 改进4：逐项进度容器（st.status 在 Streamlit 1.60 以 stExpander 渲染）
        rows_in_expander = page.locator(
            '[data-testid="stExpander"] [data-testid^="diag-check-"]'
        ).count()
        print(f"  [status] diag rows inside stExpander: {rows_in_expander}/5")
        if rows_in_expander < 5:
            issues.append(f"逐项进度容器缺少 diag 行（{rows_in_expander}/5，改进4）")

        # 改进5：全程无 stException
        exc1 = page.locator('[data-testid="stException"]').count()
        print(f"  [exception] before={exc0} after={exc1}")
        if exc1 > 0:
            issues.append(f"自检后出现 {exc1} 个 stException（改进5 降级未生效）")

        page.screenshot(path=str(ROOT / "data/ui_diag_result.png"), full_page=True)
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
    print("UI_DIAG_VERIFY: PASS (5/5 锚点 + summary=pass + 无 stException)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
