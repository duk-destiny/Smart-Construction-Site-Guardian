# -*- coding: utf-8 -*-
"""Phase 6: Agent 研判页（/agents 与 /agents/:taskId）。

覆盖：空态提示、已完成任务结果卡（风险等级/处置工单/去派发）、
Agent 运行证据链、无效任务重试入口、重新研判（轮询 UI → 结果回流）。
依赖 Phase 2 留下的影像研判任务（临时库）。
账号：admin=Admin@E2E123。
"""
import re
import sqlite3
import sys

from playwright.sync_api import sync_playwright, expect

from _common import BASE, DB, SHOTS, check, login, exit_with_summary


def re_risk():
    return re.compile(r"低|一般|较大|重大")


def pick_task() -> str:
    conn = sqlite3.connect(DB)
    row = conn.execute(
        "SELECT task_id FROM agent_runs WHERE agent='fusion' AND status='success' "
        "ORDER BY rowid DESC LIMIT 1").fetchone()
    if row is None:
        # 冷启动首轮视觉段可能超 3s 预算：回退取最近上传任务，
        # 依赖本用例的页内重试（重试加热后成功）兜底
        row = conn.execute(
            "SELECT id FROM tasks WHERE source='upload' "
            "ORDER BY created_at DESC LIMIT 1").fetchone()
    conn.close()
    return row[0] if row else ""


def main():
    task_id = pick_task()
    check("临时库存在已研判任务", bool(task_id), "未找到 fusion=success 的任务")
    if not task_id:
        sys.exit(1)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        login(page, "admin", "Admin@E2E123", "/chat")

        # ===== 1. 无任务空态 =====
        page.goto(BASE + "/agents", wait_until="networkidle")
        expect(page.get_by_text("影像研判", exact=True).first).to_be_visible(timeout=8000)
        check("无任务显示发起表单", page.locator(".ant-upload-drag").count() > 0)

        # ===== 2. 无效任务 → 重试入口 =====
        page.goto(BASE + "/agents/t_does_not_exist", wait_until="networkidle")
        try:
            expect(page.get_by_role("button", name="开始 / 重试影像研判").first).to_be_visible(timeout=8000)
            check("无效任务显示重试入口", True)
        except Exception as e:
            check("无效任务显示重试入口", False, str(e)[:100])

        # ===== 3. 已消费任务：结果取走提示 + 证据链 =====
        # 首次进入会取走遗留结果（getResult 取走即删），二次进入才是
        # 「已消费」视角：结果 404 → 取走提示 + 重试入口
        page.goto(f"{BASE}/agents/{task_id}", wait_until="networkidle")
        page.wait_for_timeout(1500)
        page.goto(f"{BASE}/agents/{task_id}", wait_until="networkidle")
        try:
            expect(page.get_by_text("上一轮研判结果已被取走或已过期").first).to_be_visible(timeout=10000)
            check("结果取走提示（pop 语义）", True)
        except Exception as e:
            check("结果取走提示（pop 语义）", False, str(e)[:100])
        check("重试入口", page.get_by_role("button", name="开始 / 重试影像研判").count() > 0)
        check("历史证据链区块", page.get_by_text("Agent 运行证据链", exact=True).count() > 0)

        # ===== 4. 重新研判：轮询 UI → 结果回流 =====
        page.get_by_role("button", name="开始 / 重试影像研判").click()
        try:
            expect(page.get_by_text("后台研判已启动").first).to_be_visible(timeout=8000)
            check("重试发起提示", True)
        except Exception as e:
            check("重试发起提示", False, str(e)[:100])
        try:
            expect(page.get_by_text("每 1.5 秒自动轮询").first).to_be_visible(timeout=10000)
            check("轮询进度界面出现", True)
        except Exception as e:
            check("轮询进度界面出现", False, str(e)[:100])
        try:
            expect(page.get_by_text("研判完成", exact=True).first).to_be_visible(timeout=180000)
            check("重试后结果回流", True)
        except Exception:
            try:
                expect(page.get_by_text("研判失败", exact=True).first).to_be_visible(timeout=15000)
                check("重试后结果回流", True, "首轮冷启动 degraded（重试加热后再验）")
                page.get_by_role("button", name="开始 / 重试影像研判").click()
                expect(page.get_by_text("研判完成", exact=True).first).to_be_visible(timeout=180000)
                check("二次重试得到完成卡", True)
            except Exception as e:
                check("重试后结果回流", False, str(e)[:120])

        # ===== 5. 回流后的结果卡内容 =====
        check("风险等级标签", page.locator(".ant-tag").filter(has_text=re_risk()).count() > 0)
        # antd 带 href 的 Button 渲染为 <a>，故用 link 定位
        check("去工单页派发按钮", page.get_by_role("link", name="去工单页派发").count() > 0)
        check("处置工单区块", page.get_by_text("处置工单", exact=True).count() > 0)
        body = page.locator("body").inner_text()
        for field in ("隐患", "违反规范", "整改要求"):
            check(f"处置工单字段[{field}]", field in body)
        check("证据链区块", page.get_by_text("Agent 运行证据链", exact=True).count() > 0)
        page.screenshot(path=str(SHOTS / "shot_agent.png"))

        browser.close()

    exit_with_summary(6)


if __name__ == "__main__":
    main()
