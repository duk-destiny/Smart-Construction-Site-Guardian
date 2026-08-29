# -*- coding: utf-8 -*-
"""按 01→06 顺序以子进程串行执行 6 个 E2E 阶段脚本。

前置：先启动临时库后端（launcher.py）与前端 dev server（5173）。
从仓库根运行：python tests/e2e/run_all.py
全部通过退出 0；任一阶段失败退出 1（仍继续执行后续阶段，
便于一次拿到全部失败信息；注意阶段间存在数据依赖，前置失败会连带后续）。
"""
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = [
    "test_01_login.py",
    "test_02_report.py",
    "test_03_orders.py",
    "test_04_realtime_history.py",
    "test_05_admin.py",
    "test_06_agent.py",
]


def main():
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    codes = {}
    for name in SCRIPTS:
        print(f"\n########## {name} ##########", flush=True)
        proc = subprocess.run([sys.executable, str(HERE / name)], env=env)
        codes[name] = proc.returncode

    print("\n=== E2E 全阶段汇总 ===", flush=True)
    for name, code in codes.items():
        print(f"{'PASS' if code == 0 else 'FAIL'} | {name} (exit={code})")
    fails = [n for n, c in codes.items() if c != 0]
    print(f"总计: {len(SCRIPTS) - len(fails)}/{len(SCRIPTS)} 个阶段通过")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
