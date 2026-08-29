# -*- coding: utf-8 -*-
"""Phase 2: 统一上报页——影像研判上传/文字线索/对话查询。

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
        page.wait_for_url("**/report", timeout=10000)
        return
    except Exception:
        pass
    if "/change-password" in page.url:
        page.get_by_label("原密码", exact=True).fill("admin123")
        page.get_by_label("新密码", exact=True).fill("Admin@E2E123")
        page.get_by_label("确认新密码", exact=True).fill("Admin@E2E123")
        page.get_by_role("button", name="提交修改").click()
        page.wait_for_url("**/report", timeout=10000)


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        login_admin(page)

        # 1. 页面结构：标题 + 三个子页签
        expect(page.get_by_text("统一上报", exact=True).first).to_be_visible(timeout=8000)
        tabs = ["影像研判", "文字线索", "对话查询"]
        missing = [t for t in tabs if page.get_by_text(t).count() == 0]
        check("上报页三页签齐全", not missing, f"missing={missing}")

        # 2. 影像研判：表单元素
        check("场景选择器存在", page.locator(".ant-select").first.is_visible())
        check("上传拖拽区存在", page.locator(".ant-upload-drag").count() > 0)
        check("作业票信息区块存在", page.get_by_text("作业票信息").count() > 0)
        check("自动研判开关存在", page.get_by_text("提交后自动启动多 Agent 研判").count() > 0)

        # 3. 上传图片 → 提交 → 跳转 Agent 页（夹具基于本目录）
        page.locator(".ant-upload-drag input[type=file]").set_input_files(str(FIXTURE_IMG))
        page.wait_for_timeout(500)
        page.get_by_role("button", name="开始智能研判").click()
        try:
            page.wait_for_url("**/agents/**", timeout=60000)
            CREATED_TASK["id"] = page.url.rsplit("/", 1)[-1]
            check("影像提交创建任务并跳转", True)
        except Exception as e:
            check("影像提交创建任务并跳转", False, str(e)[:120])

        # 4. Agent 页基本渲染（任务卡片/状态可见）
        if CREATED_TASK["id"]:
            page.wait_for_timeout(1500)
            body = page.locator("body").inner_text()
            check("Agent 页渲染任务信息",
                  CREATED_TASK["id"] in body or "研判" in body or "Agent" in body)
            page.screenshot(path=str(SHOTS / "shot_agent_run.png"))

        # 5. 回到上报页 → 文字线索页签
        page.goto(f"{BASE}/report", wait_until="networkidle")
        page.get_by_text("文字线索").last.click()
        page.wait_for_timeout(600)
        check("文字线索提示文案", page.get_by_text("摄像头拍不到的隐患").count() > 0)

        # 6. 文字隐患建单
        page.locator(".ant-select").nth(1).click()  # 隐患类别下拉
        expect(page.locator(".ant-select-item-option").first).to_be_visible(timeout=8000)
        opt = page.locator(".ant-select-item-option").first
        opt_label = opt.inner_text()
        opt.click()
        page.get_by_placeholder("例：3号楼西侧电焊机旁堆着纸箱没人清理，也没有监火人").fill(
            "E2E 测试：3号楼西侧电焊机旁堆放纸箱，无监火人在场")
        page.get_by_role("button", name="创建文字隐患单").click()
        try:
            expect(page.get_by_text("已建单", exact=False).first).to_be_visible(timeout=15000)
            check("文字隐患建单成功", True)
            body = page.locator("body").inner_text()
            m = re.search(r"已建单\s*(\S+)", body)
            if m:
                CREATED_ORDER["id"] = m.group(1)
            check("建单展示工单号与风险等级",
                  CREATED_ORDER["id"] is not None and "风险等级" in body)
            check("建单卡片含去派发入口", page.get_by_text("去工单页派发").count() > 0)
        except Exception as e:
            check("文字隐患建单成功", False, str(e)[:120])

        # 7. 对话查询页签
        page.get_by_text("对话查询").last.click()
        page.wait_for_timeout(800)
        check("对话页只读提示", page.get_by_text("对话式只读查询").count() > 0)
        search = page.get_by_placeholder("如：近7天有多少张未闭环工单")
        search.fill("近7天有多少张未闭环工单")
        # antd 两字按钮会插空格（"查 询"），用 class 定位；首次调用冷加载 LLM 较慢
        page.locator(".ant-input-search-button").click(timeout=180000)
        page.wait_for_timeout(3000)
        try:
            expect(page.get_by_text("理解方式").first).to_be_visible(timeout=120000)
            understood = True
        except Exception:
            understood = False
        body = page.locator("body").inner_text()
        check("对话查询返回结果",
              understood and ("工单" in body or "存量" in body or "暂无" in body))
        page.screenshot(path=str(SHOTS / "shot_report_chat.png"))

        browser.close()

    code = summary(2)
    print("task_id =", CREATED_TASK["id"], "| order_task =", CREATED_ORDER["id"])
    # 阶段产物落盘，供排查阶段间数据依赖（已加入 .gitignore）
    with open(HERE / "phase2_ids.txt", "w", encoding="utf-8") as f:
        f.write(f"{CREATED_TASK['id'] or ''}\n{CREATED_ORDER['id'] or ''}\n")
    sys.exit(code)


if __name__ == "__main__":
    main()
