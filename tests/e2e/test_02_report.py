# -*- coding: utf-8 -*-
"""Phase 2: AI 助手对话窗口 + 影像研判窗口（v2.2 双窗口）。

前置：临时库已存在改密后账号 admin=Admin@E2E123（Phase 1 产物）。
若密码不符（重跑场景），先走改密兜底。
"""
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

from _common import BASE, SHOTS, FIXTURE_IMG, check, summary

HERE = Path(__file__).resolve().parent
CREATED_TASK = {"id": None}
CREATED_ORDER = {"id": None}


def login_admin(page):
    page.goto(f"{BASE}/login", wait_until="networkidle")
    page.get_by_placeholder("用户名").fill("admin")
    page.get_by_placeholder("密码").fill("Admin@E2E123")
    page.get_by_role("button", name="进入系统").click()
    try:
        page.wait_for_url("**/chat", timeout=10000)
        return
    except Exception:
        pass
    if "/change-password" in page.url:
        page.get_by_label("原密码", exact=True).fill("admin123")
        page.get_by_label("新密码", exact=True).fill("Admin@E2E123")
        page.get_by_label("确认新密码", exact=True).fill("Admin@E2E123")
        page.get_by_role("button", name="提交修改").click()
        page.wait_for_url("**/chat", timeout=10000)


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        login_admin(page)

        # ===== 1. AI 助手窗口（登录默认落地页）=====
        expect(page.get_by_text("AI 助手", exact=True).first).to_be_visible(timeout=8000)
        check("AI 助手窗口渲染", page.get_by_placeholder(
            "输入问题，或上传影像让 AI 分析（Enter 发送，Shift+Enter 换行）").is_visible())
        check("会话侧栏新建按钮", page.get_by_role("button", name="新建对话").count() > 0)
        check("工具抽屉入口", page.get_by_role("button", name="工具").count() > 0)

        # ===== 2. 快捷提问（规则快路径，空库也确定性返回统计卡）=====
        page.get_by_role("button", name="本周安全统计").click()
        page.wait_for_timeout(2500)
        body = page.locator("body").inner_text()
        check("快捷查询返回结果", "检测帧" in body, body[:160])
        page.screenshot(path=str(SHOTS / "shot_chat_fast.png"))

        # ===== 3. 工具抽屉 · 文字线索建单 =====
        page.get_by_role("button", name="工具").click()
        drawer = page.locator(".ant-drawer-content")
        expect(drawer.get_by_text("文字线索建单", exact=False).first).to_be_visible(timeout=8000)
        # 场景有默认值（动火作业安全），只选隐患类别（初始为空，选项可点）
        drawer.locator(".ant-select").nth(1).click()
        expect(page.locator(".ant-select-item-option").first).to_be_visible(timeout=8000)
        page.locator(".ant-select-item-option").first.click()
        drawer.get_by_placeholder("例：电焊机旁堆着纸箱没人清理").fill(
            "E2E 测试：3号楼西侧电焊机旁堆放纸箱，无监火人在场")
        drawer.get_by_role("button", name="创建隐患单").click()
        try:
            expect(page.get_by_text("文字线索建单：", exact=False).first
                   ).to_be_visible(timeout=15000)
            check("工具卡文字建单成功", True)
            body = page.locator("body").inner_text()
            m = re.search(r"文字线索建单[：:]\s*(t_[0-9a-f]+)", body)
            if m:
                CREATED_ORDER["id"] = m.group(1)
        except Exception as e:
            check("工具卡文字建单成功", False, str(e)[:120])
        page.screenshot(path=str(SHOTS / "shot_chat_text_order.png"))

        # ===== 4. 影像研判窗口：发起表单 → 上传 → 跳转任务页 =====
        page.get_by_text("影像研判", exact=True).first.click()
        page.wait_for_url("**/agents", timeout=8000)
        check("影像研判窗口表单", page.get_by_text("作业票信息").count() > 0)
        check("场景选择器存在", page.locator(".ant-select").first.is_visible())
        check("上传拖拽区存在", page.locator(".ant-upload-drag").count() > 0)
        check("自动研判开关存在", page.get_by_text("提交后自动启动影像研判").count() > 0)

        # 限定影像研判表单内的上传框（避开抽屉/动画期的其他 Dragger）
        form_input = page.locator("form .ant-upload-drag input[type=file]").first
        form_input.wait_for(state="attached", timeout=10000)
        page.wait_for_timeout(600)
        form_input.set_input_files(str(FIXTURE_IMG))
        page.wait_for_timeout(800)
        page.get_by_role("button", name="开始智能研判").click()
        try:
            page.wait_for_url("**/agents/**", timeout=60000)
            CREATED_TASK["id"] = page.url.rsplit("/", 1)[-1]
            check("影像提交创建任务并跳转", True)
        except Exception as e:
            body_txt = page.locator("body").inner_text()[:150]
            check("影像提交创建任务并跳转", False,
                  str(e)[:120] + " | url=" + page.url + " | body=" + body_txt)

        # ===== 5. 任务页基本渲染 =====
        if CREATED_TASK["id"]:
            page.wait_for_timeout(1500)
            body = page.locator("body").inner_text()
            check("任务页渲染研判信息",
                  CREATED_TASK["id"] in body or "研判" in body or "证据链" in body)
            page.screenshot(path=str(SHOTS / "shot_agent_run.png"))

        # ===== 6. 会话管理：新建 → 列表出现 =====
        page.get_by_text("AI 助手", exact=True).first.click()
        page.wait_for_url("**/chat", timeout=8000)
        before = page.locator(".ant-drawer-content").count()
        page.get_by_role("button", name="新建对话").click()
        page.wait_for_timeout(800)
        check("新建对话产生会话项", page.get_by_text("新对话", exact=True).count() > 0,
              f"drawer_before={before}")

        browser.close()

    code = summary(2)
    print("task_id =", CREATED_TASK["id"], "| order_task =", CREATED_ORDER["id"])
    # 阶段产物落盘，供排查阶段间数据依赖（已加入 .gitignore）
    with open(HERE / "phase2_ids.txt", "w", encoding="utf-8") as f:
        f.write(f"{CREATED_TASK['id'] or ''}\n{CREATED_ORDER['id'] or ''}\n")
    sys.exit(code)


if __name__ == "__main__":
    main()
