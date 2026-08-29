# -*- coding: utf-8 -*-
"""E2E 测试公共样板：路径常量、check() 断言收集、登录/登出与阶段汇总。

所有阶段脚本共享本模块，避免各自复制 BASE/ROOT/DB/check()/login()。
独立运行方式：在仓库根执行 `python tests/e2e/test_0N_xxx.py`。
"""
import sys
from pathlib import Path

# 前端 dev server 地址（npm run dev -- --port 5173）
BASE = "http://127.0.0.1:5173"

# 仓库根：tests/e2e/_common.py → parents[2]
ROOT = Path(__file__).resolve().parents[2]

# 临时库：由 launcher.py 启动的独立后端使用，不碰生产 data/app.db
DB = ROOT / "data" / "tmp_e2e_test.db"

# 截图输出目录（已加入 .gitignore）
SHOTS = Path(__file__).resolve().parent / "shots"
SHOTS.mkdir(exist_ok=True)

# 上传夹具图片（影像研判 / 整改附件共用）
FIXTURE_IMG = Path(__file__).resolve().parent / "fixture_smoke.jpg"

# (name, ok, detail) 三元组收集，供 summary() 汇总
RESULTS = []


def check(name, ok, detail=""):
    """记录一条断言并即时打印；失败时附带 detail 便于排查。"""
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'} | {name}"
          + (f" | {detail}" if detail and not ok else ""), flush=True)


def login(page, username, password, expect_url=None):
    """走登录页表单登录；给定 expect_url 时等待落地页跳转。"""
    page.goto(f"{BASE}/login", wait_until="networkidle")
    page.get_by_placeholder("用户名").fill(username)
    page.get_by_placeholder("密码").fill(password)
    page.get_by_role("button", name="进入系统").click()
    if expect_url:
        page.wait_for_url(f"**{expect_url}", timeout=12000)


def logout(page, username):
    """顶栏用户名菜单 → 退出登录，等待回到 /login。"""
    page.locator("header").get_by_text(username, exact=True).first.click()
    page.get_by_text("退出登录").click()
    page.wait_for_url("**/login", timeout=8000)


def summary(phase):
    """打印 `=== PhaseN 结果 ===` 汇总并返回进程退出码（有失败=1）。"""
    fails = [r for r in RESULTS if not r[1]]
    print(f"\n=== Phase{phase} 结果: {len(RESULTS) - len(fails)}/{len(RESULTS)} 通过 ===")
    return 1 if fails else 0


def exit_with_summary(phase):
    sys.exit(summary(phase))
