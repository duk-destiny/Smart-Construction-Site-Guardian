# -*- coding: utf-8 -*-
"""Phase 3: 工单闭环——派发 / 整改提交 / 验收通过 / 驳回重改 全链路。

依赖 Phase 2 已在临时库留下 open 工单（文字隐患单 + 影像研判单）。
账号：admin=Admin@E2E123，lisi=Lisi@E2E123（Phase 1 改密产物）。
"""
import re
import sys

from playwright.sync_api import sync_playwright, expect

from _common import BASE, SHOTS, FIXTURE_IMG, check, login, logout, summary


def switch_orders_tab(page, label):
    page.get_by_text(label, exact=True).first.click()
    page.wait_for_timeout(500)


def dispatch_first_open_order(page):
    """台账里挑一条 open 且未派发的工单，点开抽屉派发给 lisi，返回工单号。"""
    rows = page.locator(".ant-table-tbody tr.ant-table-row")
    target = None
    for i in range(rows.count()):
        row = rows.nth(i)
        if "待整改" in row.inner_text():
            target = row
            break
    if target is None:
        return None
    order_id = target.locator("td").first.inner_text().strip()
    target.click()
    drawer = page.locator(".ant-drawer-open")
    expect(drawer).to_be_visible(timeout=5000)
    # 派发表单异步加载：等真正的派发 <form>（含“责任人”）出现，避免误点改判下拉
    form = drawer.locator("form").first
    expect(form.get_by_text("责任人")).to_be_visible(timeout=8000)
    sel = form.locator(".ant-select").first
    sel.click()
    page.locator(".ant-select-item-option").filter(has_text="lisi").first.click()
    # 时限保持默认
    form.get_by_role("button", name="派发 / 改派").click()
    expect(page.locator(".ant-message-success").first).to_be_visible(timeout=10000)
    page.locator(".ant-drawer-open .ant-drawer-close").click()
    page.wait_for_timeout(400)
    return order_id


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})

        # ===== 1. admin：工单页结构 =====
        login(page, "admin", "Admin@E2E123", "/report")
        page.goto(BASE + "/orders", wait_until="networkidle")
        expect(page.get_by_text("工单闭环", exact=True).first).to_be_visible(timeout=8000)
        for label in ("台账与派发", "待验收", "逾期"):
            check(f"页签[{label}]存在", page.get_by_text(label, exact=True).count() > 0)

        # 台账有数据（Phase 2 建单）
        rows = page.locator(".ant-table-tbody tr.ant-table-row")
        check("台账有工单数据", rows.count() > 0)

        # ===== 2. admin：派发第一张单给 lisi =====
        order_a = dispatch_first_open_order(page)
        check("抽屉派发成功", order_a is not None, "未找到可派发工单")
        dispatched_id = order_a

        # ===== 3. lisi：我的整改单出现该单并提交整改 =====
        logout(page, "admin")
        login(page, "lisi", "Lisi@E2E123", "/my-orders")
        expect(page.get_by_text("我的整改单", exact=True).first).to_be_visible(timeout=8000)
        try:
            expect(page.get_by_text("待整改").first).to_be_visible(timeout=8000)
            check("lisi 收到整改单", True)
        except Exception as e:
            check("lisi 收到整改单", False, str(e)[:100])

        page.get_by_placeholder("整改说明（必填，如：已清理现场并补充灭火器）").fill(
            "E2E 整改：已清理纸箱并安排监火人到场")
        page.locator(".ant-upload input[type=file]").first.set_input_files(str(FIXTURE_IMG))
        page.wait_for_timeout(500)
        page.get_by_role("button", name="提交整改，等待验收").click()
        try:
            expect(page.locator(".ant-message-success").first).to_be_visible(timeout=15000)
            check("lisi 提交整改成功", True)
        except Exception as e:
            check("lisi 提交整改成功", False, str(e)[:100])
        page.wait_for_timeout(600)
        check("提交后显示等待验收",
              page.get_by_text("等待安全员验收").count() > 0
              or page.get_by_text("待验收").count() > 0)

        # ===== 4. admin：驳回一次（驳回重改链路）=====
        logout(page, "lisi")
        login(page, "admin", "Admin@E2E123", "/report")
        page.goto(BASE + "/orders", wait_until="networkidle")
        switch_orders_tab(page, "待验收")
        rows = page.locator(".ant-table-tbody tr.ant-table-row")
        check("待验收列表有单", rows.count() > 0)
        rows.first.get_by_role("button", name="驳 回").click()
        modal = page.locator(".ant-modal").last
        modal.locator("textarea").fill("E2E 驳回：整改照片不清晰，请重新拍摄")
        modal.get_by_role("button", name="确认驳回").click()
        expect(page.locator(".ant-message-success").first).to_be_visible(timeout=10000)
        check("admin 驳回成功", True)

        # ===== 5. lisi：看到驳回原因，再次提交 =====
        logout(page, "admin")
        login(page, "lisi", "Lisi@E2E123", "/my-orders")
        try:
            expect(page.get_by_text("驳回原因").first).to_be_visible(timeout=8000)
            check("lisi 看到驳回原因", True)
        except Exception as e:
            check("lisi 看到驳回原因", False, str(e)[:100])
        page.get_by_placeholder("整改说明（必填，如：已清理现场并补充灭火器）").fill(
            "E2E 二次整改：已重新拍摄并确认现场无隐患")
        page.get_by_role("button", name="提交整改，等待验收").click()
        expect(page.locator(".ant-message-success").first).to_be_visible(timeout=15000)
        check("lisi 二次提交成功", True)

        # ===== 6. admin：验收通过 =====
        logout(page, "lisi")
        login(page, "admin", "Admin@E2E123", "/report")
        page.goto(BASE + "/orders", wait_until="networkidle")
        switch_orders_tab(page, "待验收")
        rows = page.locator(".ant-table-tbody tr.ant-table-row")
        check("二次提交回到待验收", rows.count() > 0)
        rows.first.get_by_role("button", name="通 过").click()
        page.get_by_role("button", name=re.compile(r"确\s*定")).click()
        expect(page.locator(".ant-message-success").first).to_be_visible(timeout=10000)
        check("admin 验收通过销项", True)
        page.wait_for_timeout(600)
        check("待验收列表清空",
              page.locator(".ant-table-tbody tr.ant-table-row").count() == 0
              or page.get_by_text("暂无数据").count() > 0)

        # ===== 7. 台账中该单状态=已销项；逾期页签可渲染 =====
        switch_orders_tab(page, "台账与派发")
        body = page.locator(".ant-table").inner_text()
        check("台账出现已销项状态", "已销项" in body)
        switch_orders_tab(page, "逾期")
        page.wait_for_timeout(400)
        check("逾期页签正常渲染",
              page.locator(".ant-table").count() > 0 or page.get_by_text("暂无数据").count() > 0)
        page.screenshot(path=str(SHOTS / "shot_orders.png"))

        browser.close()

    code = summary(3)
    print("dispatched:", dispatched_id or '-')
    sys.exit(code)


if __name__ == "__main__":
    main()
