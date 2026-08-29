# -*- coding: utf-8 -*-
"""Phase 5: 管理端 7 个子页签全功能测试。

用户治理（新建/停用/重置密码）、模型版本、知识库（导入 PDF）、
推送通道（测试推送/捕获）、系统自检、审计日志（列表/导出 CSV）、纠偏样本。
账号：admin=Admin@E2E123。测试产生的用户 e2e_worker 留在临时库（销毁即可）。
"""
import re

from playwright.sync_api import sync_playwright, expect

from _common import BASE, ROOT, SHOTS, check, login, logout, exit_with_summary

PDF = ROOT / "docs" / "说明文档.pdf"


def switch_tab(page, label):
    page.locator(".admin-tab", has_text=label).first.click()
    page.wait_for_timeout(700)


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        login(page, "admin", "Admin@E2E123", "/report")
        page.goto(BASE + "/admin", wait_until="networkidle")
        expect(page.get_by_text("管理端", exact=True).first).to_be_visible(timeout=8000)
        for t in ("用户治理", "模型版本", "知识库", "推送通道", "系统自检", "审计日志", "纠偏样本"):
            check(f"页签[{t}]渲染", page.locator(".admin-tab", has_text=t).count() > 0)

        # ===== 1. 用户治理 =====
        rows = page.locator(".ant-table-tbody tr.ant-table-row")
        try:
            expect(rows.first).to_be_visible(timeout=8000)
            check("用户表格有种子账号", rows.count() >= 3)
        except Exception as e:
            check("用户表格有种子账号", False, str(e)[:100])

        # 新建用户
        page.get_by_role("button", name="＋ 新建用户").click()
        modal = page.locator(".ant-modal").last
        modal.get_by_label("用户名（2-32 字符）").fill("e2e_worker")
        modal.get_by_label("初始密码（至少 8 位）").fill("E2eWorker@123")
        modal.locator(".ant-select").first.click()
        page.locator(".ant-select-item-option").filter(has_text="safety · 安全员").first.click()
        modal.get_by_role("button", name="创 建").click()
        try:
            expect(page.locator(".ant-message-success").first).to_be_visible(timeout=8000)
            check("新建用户成功", True)
        except Exception as e:
            check("新建用户成功", False, str(e)[:100])
        page.wait_for_timeout(600)
        check("新用户出现在表格",
              rows.filter(has_text="e2e_worker").count() == 1)

        # 停用 → 启用
        w = rows.filter(has_text="e2e_worker").first
        w.get_by_role("button", name=re.compile(r"停\s*用")).click()
        page.get_by_role("button", name=re.compile(r"确\s*定")).click()
        page.wait_for_timeout(800)
        check("停用后状态变更",
              "停用" in rows.filter(has_text="e2e_worker").first.inner_text())
        w = rows.filter(has_text="e2e_worker").first
        w.get_by_role("button", name=re.compile(r"启\s*用"), exact=True).click()
        page.get_by_role("button", name=re.compile(r"确\s*定")).click()
        page.wait_for_timeout(800)
        check("启用后状态恢复",
              "正常" in rows.filter(has_text="e2e_worker").first.inner_text())

        # 重置密码
        w = rows.filter(has_text="e2e_worker").first
        w.get_by_role("button", name="重置密码").click()
        modal = page.locator(".ant-modal").last
        modal.get_by_label(re.compile("新密码（至少 8 位")).fill("E2eReset@456")
        modal.get_by_role("button", name="重 置").click()
        try:
            expect(page.get_by_text("已重置，对方下次登录将强制改密").first).to_be_visible(timeout=8000)
            check("重置密码成功", True)
        except Exception as e:
            check("重置密码成功", False, str(e)[:100])

        # ===== 2. 模型版本 =====
        switch_tab(page, "模型版本")
        page.wait_for_timeout(600)
        check("模型表格渲染", page.locator(".ant-table-tbody tr.ant-table-row").count() >= 1)
        check("活跃版本描述渲染", page.get_by_text("当前活跃版本").count() > 0)
        check("使用中标记", page.get_by_text("使用中").count() >= 1)

        # ===== 3. 知识库：导入 PDF =====
        switch_tab(page, "知识库")
        check("知识库导入按钮", page.get_by_text("导入规范 PDF").count() > 0)
        page.locator("input[type=file]").first.set_input_files(str(PDF))
        try:
            expect(page.get_by_text(re.compile("导入成功")).first).to_be_visible(timeout=60000)
            check("PDF 导入成功", True)
        except Exception as e:
            check("PDF 导入成功", False, str(e)[:100])
        page.wait_for_timeout(600)
        kb_rows = page.locator(".ant-table-tbody tr.ant-table-row")
        check("知识库文档列表更新", kb_rows.count() >= 1
              and "说明文档" in kb_rows.first.inner_text())

        # ===== 4. 推送通道 =====
        switch_tab(page, "推送通道")
        page.wait_for_timeout(700)
        check("推送状态描述渲染", page.get_by_text("推送开关").count() > 0)
        page.get_by_role("button", name="发送测试推送").click()
        try:
            expect(page.get_by_text(re.compile("测试推送结果")).first).to_be_visible(timeout=15000)
            check("测试推送返回结果", True)
        except Exception as e:
            check("测试推送返回结果", False, str(e)[:100])

        # ===== 5. 系统自检 =====
        switch_tab(page, "系统自检")
        page.get_by_role("button", name="运行系统自检").click()
        try:
            expect(page.locator(".ant-table-tbody tr.ant-table-row").first).to_be_visible(timeout=30000)
            check("系统自检出结果", page.locator(".ant-table-tbody tr.ant-table-row").count() >= 1)
        except Exception as e:
            check("系统自检出结果", False, str(e)[:100])

        # ===== 6. 审计日志 =====
        switch_tab(page, "审计日志")
        page.wait_for_timeout(700)
        audit_rows = page.locator(".ant-table-tbody tr.ant-table-row")
        try:
            expect(audit_rows.first).to_be_visible(timeout=8000)
            check("审计日志有流水", audit_rows.count() > 0)
            check("审计含用户创建动作", "user_create" in page.locator(".ant-table").inner_text()
                  or "audit" in page.locator(".ant-table").inner_text())
        except Exception as e:
            check("审计日志有流水", False, str(e)[:100])
        with page.expect_download(timeout=15000) as dl:
            page.get_by_role("button", name="导出审计 CSV").click()
        check("审计 CSV 可导出", dl.value is not None)

        # ===== 7. 纠偏样本 =====
        switch_tab(page, "纠偏样本")
        page.wait_for_timeout(700)
        check("纠偏样本页签渲染", page.get_by_role("button", name="导出纠偏 CSV").count() > 0)

        # ===== 8. 新用户 e2e_worker 首登强制改密验证 =====
        logout(page, "admin")
        login(page, "e2e_worker", "E2eReset@456", "/change-password")
        check("被重置用户首登强制改密", "/change-password" in page.url)

        page.screenshot(path=str(SHOTS / "shot_admin.png"))
        browser.close()

    exit_with_summary(5)


if __name__ == "__main__":
    main()
