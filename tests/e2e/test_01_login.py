# -*- coding: utf-8 -*-
"""Phase 1: 登录 / 首登强制改密 / 角色路由守卫。"""
from playwright.sync_api import sync_playwright, expect

from _common import BASE, SHOTS, check, login, exit_with_summary


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})

        # 1. 根路径重定向到登录页
        page.goto(BASE, wait_until="networkidle")
        check("根路径重定向 /login", "/login" in page.url, page.url)

        # 2. 登录页关键元素
        try:
            expect(page.get_by_role("heading", name="智护工地")).to_be_visible(timeout=5000)
            check("登录页标题渲染", True)
        except Exception as e:
            check("登录页标题渲染", False, str(e)[:120])
        check("登录页表单完整",
              page.get_by_placeholder("用户名").is_visible()
              and page.get_by_placeholder("密码").is_visible()
              and page.get_by_role("button", name="进入系统").is_visible())

        # 3. 错误密码 → toast 报错且不跳转（等后端 401 回来，可能 >800ms）
        login(page, "admin", "wrong_password_1")
        toast = page.locator(".ant-message-error, .ant-message-notice")
        try:
            expect(toast.first).to_be_visible(timeout=6000)
            check("错误密码出现提示", True)
        except Exception as e:
            check("错误密码出现提示", False, str(e)[:100])
        check("错误密码不跳转", "/login" in page.url, page.url)

        # 4. admin 首登 → 强制改密页
        login(page, "admin", "admin123")
        page.wait_for_url("**/change-password", timeout=8000)
        check("admin 首登强制改密", "/change-password" in page.url, page.url)

        # 5. 改密表单：两次不一致 → 报错
        page.get_by_label("原密码", exact=True).fill("admin123")
        page.get_by_label("新密码", exact=True).fill("Admin@E2E123")
        page.get_by_label("确认新密码", exact=True).fill("Admin@E2E456")
        page.get_by_role("button", name="提交修改").click()
        page.wait_for_timeout(800)
        check("两次密码不一致报错",
              page.locator(".ant-message-error").count() > 0
              or page.locator(".ant-form-item-explain-error").count() > 0)
        check("不一致未跳转", "/change-password" in page.url)

        # 6. 正确改密 → 进入 admin 首页 /chat
        page.get_by_label("确认新密码", exact=True).fill("Admin@E2E123")
        page.get_by_role("button", name="提交修改").click()
        page.wait_for_url("**/chat", timeout=8000)
        check("改密成功进入 /chat", "/chat" in page.url, page.url)

        # 7. admin 布局：顶栏品牌 + Dock 六项
        check("顶栏品牌可见", page.get_by_text("智护工地", exact=True).first.is_visible())
        dock_labels = ["AI 助手", "影像研判", "工单闭环", "历史分析", "实时监测", "管理端"]
        missing = [t for t in dock_labels if page.get_by_text(t, exact=True).count() == 0]
        check("admin Dock 六项齐全", not missing, f"missing={missing}")

        # 8. 退出登录
        page.get_by_text("admin", exact=True).first.click()
        page.get_by_text("退出登录").click()
        page.wait_for_url("**/login", timeout=8000)
        check("退出登录回到 /login", "/login" in page.url, page.url)

        # 9. 新密码再登录 → 不再强制改密，直达 /chat
        login(page, "admin", "Admin@E2E123")
        page.wait_for_url("**/chat", timeout=8000)
        check("新密码登录直达 /chat", "/chat" in page.url, page.url)

        # 10. 旧密码应失效
        page.get_by_text("admin", exact=True).first.click()
        page.get_by_text("退出登录").click()
        page.wait_for_url("**/login", timeout=8000)
        login(page, "admin", "admin123")
        page.wait_for_timeout(1000)
        check("旧密码已失效", "/login" in page.url, page.url)

        # 11. lisi(responsible) 首登 → 改密 → /my-orders，Dock 仅一项
        login(page, "lisi", "demo1234")
        page.wait_for_url("**/change-password", timeout=8000)
        page.get_by_label("原密码", exact=True).fill("demo1234")
        page.get_by_label("新密码", exact=True).fill("Lisi@E2E123")
        page.get_by_label("确认新密码", exact=True).fill("Lisi@E2E123")
        page.get_by_role("button", name="提交修改").click()
        page.wait_for_url("**/my-orders", timeout=8000)
        check("responsible 落地 /my-orders", "/my-orders" in page.url, page.url)
        check("responsible Dock 仅我的整改单",
              page.get_by_text("我的整改单", exact=True).count() > 0
              and page.get_by_text("管理端", exact=True).count() == 0)

        # 12. responsible 直闯 /admin 被守卫拦回
        page.goto(f"{BASE}/admin", wait_until="networkidle")
        page.wait_for_timeout(600)
        check("/admin 守卫拦回 /my-orders", "/my-orders" in page.url, page.url)

        # 13. safety 首登 → /chat，无管理端入口
        page.get_by_text("lisi", exact=True).first.click()
        page.get_by_text("退出登录").click()
        page.wait_for_url("**/login", timeout=8000)
        login(page, "safety", "demo1234")
        page.wait_for_url("**/change-password", timeout=8000)
        page.get_by_label("原密码", exact=True).fill("demo1234")
        page.get_by_label("新密码", exact=True).fill("Safety@E2E123")
        page.get_by_label("确认新密码", exact=True).fill("Safety@E2E123")
        page.get_by_role("button", name="提交修改").click()
        page.wait_for_url("**/chat", timeout=8000)
        check("safety 落地 /chat", "/chat" in page.url, page.url)
        check("safety 无管理端入口", page.get_by_text("管理端", exact=True).count() == 0)

        page.screenshot(path=str(SHOTS / "shot_after_login.png"))
        browser.close()

    exit_with_summary(1)


if __name__ == "__main__":
    main()
