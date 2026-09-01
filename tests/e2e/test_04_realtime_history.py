# -*- coding: utf-8 -*-
"""Phase 4: 实时监测（告警列表/状态流转/转工单）+ 历史分析（图表/风险记录/日期联动）。

前置：临时库（hub 未启用 → 告警列表模式）。告警数据由本脚本以夹具方式
写入临时库（检测管线需要 RTSP 流，测试环境无法自然产生）。
账号：admin=Admin@E2E123。
"""
import re
import sqlite3

from playwright.sync_api import sync_playwright, expect

from _common import BASE, DB, SHOTS, check, login, logout, exit_with_summary

# 相对路径与生产格式一致（存库字段，后端按仓库根解析）
FIXTURE_IMG_DB = "data/alarms/hub_0_smoke_20260829_001127_460.jpg"


def seed_alarms():
    """向临时库写入 3 条告警夹具：new带图 / new无图 / confirmed。"""
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("DELETE FROM alarm_events")
    rows = [
        ("al_e2e000000001", "smoke", 0.93, FIXTURE_IMG_DB, "demo://", "new"),
        ("al_e2e000000002", "spark", 0.88, None, "hub:0", "new"),
        ("al_e2e000000003", "no_hardhat", 0.81, None, "hub:0", "confirmed"),
    ]
    for aid, cls, conf, img, src, st in rows:
        cur.execute(
            "INSERT INTO alarm_events(id,session_id,task_id,scene_id,cls,conf,"
            "image_path,source,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,datetime('now'))",
            (aid, "e2e_session", None, "hot_work", cls, conf, img, src, st))
    conn.commit()
    conn.close()


def main():
    seed_alarms()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        login(page, "admin", "Admin@E2E123", "/chat")

        # ===== 1. 实时监测页结构 =====
        page.goto(BASE + "/realtime", wait_until="networkidle")
        expect(page.get_by_text("实时监测", exact=True).first).to_be_visible(timeout=8000)
        check("实时监测页头渲染", True)
        for label in ("HUB", "观看者", "推理帧", "告警"):
            check(f"统计卡片[{label}]", page.get_by_text(label, exact=True).count() > 0)
        check("Hub未启用提示", page.get_by_text("实时 Hub 未启用").count() > 0)

        # ===== 2. 告警表格 =====
        rows = page.locator(".ant-table-tbody tr.ant-table-row")
        try:
            expect(rows.first).to_be_visible(timeout=8000)
            check("告警列表有数据", rows.count() == 3, f"rows={rows.count()}")
        except Exception as e:
            check("告警列表有数据", False, str(e)[:100])
        check("证据缩略图渲染", page.locator(".ant-table img[src*='media']").count() > 0
              or page.locator(".ant-table-tbody img").count() > 0)

        # ===== 3. 告警状态更新：smoke → 误报 =====
        smoke_row = rows.filter(has_text="smoke").first
        smoke_row.locator(".ant-select").first.click()
        page.locator(".ant-select-item-option").filter(has_text="误报").first.click()
        try:
            expect(page.locator(".ant-message-success").first).to_be_visible(timeout=8000)
            check("标记误报成功", True)
        except Exception as e:
            check("标记误报成功", False, str(e)[:100])
        page.wait_for_timeout(600)
        check("误报状态生效",
              rows.filter(has_text="smoke").first.inner_text().find("误报") >= 0)

        # ===== 4. 转工单：spark → 整改工单 =====
        spark_row = rows.filter(has_text="spark").first
        spark_row.get_by_role("button", name="转工单").click()
        page.get_by_role("button", name=re.compile(r"确\s*定")).click()
        try:
            expect(page.locator(".ant-message-success").first).to_be_visible(timeout=8000)
            check("告警转工单成功", True)
        except Exception as e:
            check("告警转工单成功", False, str(e)[:100])
        page.wait_for_timeout(600)
        body = page.locator(".ant-table").inner_text()
        check("转工单后状态更新", ("已确认" in body or "confirmed" in body))

        # ===== 5. 历史分析页 =====
        page.goto(BASE + "/history", wait_until="networkidle")
        expect(page.get_by_text("历史分析", exact=True).first).to_be_visible(timeout=8000)
        check("历史分析页头渲染", True)
        check("日期范围选择器", page.locator(".ant-picker").count() >= 1)
        check("合规率趋势图表", page.get_by_text("合规率趋势", exact=True).count() > 0)
        check("隐患类别分布图表", page.get_by_text("隐患类别分布", exact=True).count() > 0)
        check("任务风险记录表格", page.get_by_text("任务风险记录", exact=True).count() > 0)
        page.wait_for_timeout(1500)  # ECharts 动画/渲染
        check("ECharts canvas 渲染", page.locator("canvas").count() >= 2,
              f"canvas={page.locator('canvas').count()}")

        # 任务风险记录表有 Phase 2 的任务
        risk_rows = page.locator(".ant-table-tbody tr.ant-table-row")
        try:
            expect(risk_rows.first).to_be_visible(timeout=8000)
            check("风险记录含任务数据", risk_rows.count() > 0)
        except Exception as e:
            check("风险记录含任务数据", False, str(e)[:100])

        # ===== 6. 日期范围联动：改为近 30 天 =====
        picker = page.locator(".ant-picker").first
        picker.click()
        picker.locator("input").first.fill("2026-08-01")
        page.keyboard.press("Enter")
        picker.locator("input").nth(1).fill("2026-08-29")
        page.keyboard.press("Enter")
        page.keyboard.press("Escape")
        page.wait_for_timeout(1200)
        check("改日期后页面仍正常", page.locator("canvas").count() >= 2)

        page.screenshot(path=str(SHOTS / "shot_history.png"), full_page=True)
        logout(page, "admin")
        browser.close()

    exit_with_summary(4)


if __name__ == "__main__":
    main()
