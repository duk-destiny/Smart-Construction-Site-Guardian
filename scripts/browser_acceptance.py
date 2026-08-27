"""浏览器级真机验收（Playwright · chromium headless）。

完整走通 v0.2~v0.5 的演示故事线并逐步截图到 data/e2e_screens/：
  admin登录 → 文字上报建单 → 派发lisi → lisi提单 → admin批量验收销项
  → 时间游标逾期扫描 → Tab③对话查询 → Tab①渲染健全性 → lisi待办清零复核。
任何断言失败即退出非零。服务器由外部启动（8504），本脚本只管浏览器。

用法::
    .venv313/Scripts/python.exe scripts/browser_acceptance.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

BASE = "http://localhost:8507"
SHOTS = Path("data/e2e_screens")


def shot(page, name: str) -> None:
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=True)
    print(f"  📸 {name}.png")


def wait_app(page, timeout=30000):
    page.wait_for_load_state("networkidle", timeout=timeout)


def click_tab(page, label: str) -> None:
    page.get_by_role("tab", name=label).click()
    page.wait_for_timeout(900)


def login(page, username: str, password: str) -> None:
    page.goto(BASE)
    wait_app(page)
    page.get_by_label("用户名", exact=False).first.fill(username)
    page.get_by_label("密码", exact=False).first.fill(password)
    page.get_by_role("button", name="登录").click()
    wait_app(page)
    # 顶部状态条（👤 前缀）是唯一稳定锚：欢迎 toast 会重复文本造成双匹配
    expect(page.get_by_text(f"👤 {username}（", exact=False)) \
        .to_be_visible(timeout=15000)


def logout(page) -> None:
    page.get_by_role("button", name="退出").click()
    wait_app(page)


def step(name: str) -> None:
    print(f"▶ {name}")


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1360, "height": 900})

        # ① admin 登录
        step("① admin 登录")
        login(page, "admin", "admin123")
        shot(page, "01_admin_logged_in")

        # ② 文字上报建单（Tab②）
        step("② 文字上报建单")
        click_tab(page, "文字")
        page.get_by_label("隐患描述", exact=False).first.fill(
            "3号楼西侧电焊机旁堆放纸箱未清理，无监火人")
        page.get_by_label("位置", exact=False).first.fill("3号楼西侧")
        page.get_by_role("button", name="创建文字隐患单").click()
        wait_app(page)
        page.wait_for_timeout(2500)
        expect(page.get_by_text("派发与整改闭环")).to_be_visible(timeout=20000)
        shot(page, "02_text_report_created")

        # ③ 派发（按钮文案随状态为 派发/改派）；断言 metric 责任人=lisi
        step("③ 派发工单给 lisi")
        btn = page.get_by_role("button", name="派发 工单")
        if btn.count() == 0:
            btn = page.get_by_role("button", name="改派 工单")
        btn.first.click()
        wait_app(page)
        page.wait_for_timeout(2500)
        expect(page.locator("div[data-testid='stMetric']")
               .filter(has_text="lisi")).to_be_visible(timeout=15000)
        shot(page, "03_dispatched_to_lisi")

        # ④ lisi 登录：有🔨单则提交，全部已交则直接断言⏳在列
        step("④ lisi 登录并提交整改")
        logout(page)
        login(page, "lisi", "demo1234")
        expect(page.get_by_text("🧰 我的整改单")).to_be_visible(timeout=15000)
        for _ in range(8):
            if page.get_by_text("[🔨 待整改]").count() == 0:
                break
            page.get_by_text("[🔨 待整改]").first.click()
            page.wait_for_timeout(900)
            page.get_by_label("整改说明", exact=False).first.fill(
                "纸箱已清运至指定垃圾站，现场已配备监火人与灭火器")
            page.get_by_role("button", name="提交整改并申请验收").click()
            page.wait_for_timeout(2200)
            wait_app(page)
        expect(page.get_by_text("[🔨 待整改]")).to_have_count(0, timeout=15000)

        # ⑤ admin 批量验收销项（循环直至队列空）+ 逾期巡检（时间游标）
        step("⑤ admin 批量验收销项 + 逾期巡检")
        logout(page)
        login(page, "admin", "admin123")
        page.get_by_role("link", name="管理端").click()
        wait_app(page)
        expect(page.get_by_role("heading", name="工单验收队列"))             .to_be_visible(timeout=15000)
        for _ in range(12):
            if page.get_by_role("button", name="通过并销项").count() == 0:
                pend = page.get_by_text("[待验收]", exact=False)
                if pend.count() == 0:
                    break
                pend.first.click()          # 展开折叠的待验收单
                page.wait_for_timeout(900)
                continue
            page.get_by_role("button", name="通过并销项").first.click()
            page.wait_for_timeout(2200)
            wait_app(page)
        expect(page.get_by_text("暂无待验收工单")).to_be_visible(timeout=15000)
        shot(page, "05_approved_closed")

        page.get_by_role("spinbutton").first.fill("72")
        page.get_by_role("button", name="扫描逾期并催办").click()
        page.wait_for_timeout(2200)
        expect(page.get_by_text("越级升级", exact=False).first) \
            .to_be_visible(timeout=15000)
        shot(page, "05b_overdue_scan")

        # ⑥ Tab③ 对话式查询（逾期口径）
        step("⑥ Tab③ 对话式查询")
        page.get_by_role("link", name="统一上报").click()
        wait_app(page)
        click_tab(page, "工单速查")
        page.get_by_label("问一句", exact=False).first.fill("近7天有没有逾期的")
        page.keyboard.press("Enter")
        page.wait_for_timeout(2200)
        expect(page.get_by_text("存量逾期未整改", exact=False).first) \
            .to_be_visible(timeout=15000)
        shot(page, "06_dialog_query")

        # ⑦ Tab① 渲染健全性
        click_tab(page, "影像研判")
        expect(page.get_by_text("作业票信息", exact=False).first).to_be_visible()
        shot(page, "07_media_tab_ok")

        # ⑧ lisi 复核：不再有🔨待整改残留
        logout(page)
        login(page, "lisi", "demo1234")
        expect(page.get_by_text("🧰 我的整改单")).to_be_visible(timeout=15000)
        expect(page.get_by_text("[🔨 待整改]")).to_have_count(0, timeout=15000)
        shot(page, "08_lisi_clean_queue")

        browser.close()

    print("✅ 浏览器真机验收全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
