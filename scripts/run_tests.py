#!/usr/bin/env python3
"""跨平台测试运行器（本地与 CI 通用）。

把 streamlit AppTest 测试（tests/test_ui_flows.py）拆到独立进程，规避
AppTest 的 pyarrow 序列化与 torch/onnxruntime 同进程在 Windows 上触发的
原生访问违例（0xC0000005）。其余测试在主进程一次跑完。

用法:
    python scripts/run_tests.py

替代 run_tests.ps1（后者受 PowerShell 执行策略限制）。
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = ROOT / "data" / "pytest_tmp"
TMP.mkdir(parents=True, exist_ok=True)

env = os.environ.copy()
# sandbox/Windows 下默认 temp 可能不可写，统一指向仓库内
env["TMP"] = str(TMP)
env["TEMP"] = str(TMP)
env["TMPDIR"] = str(TMP)
env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
# 限制原生库 OpenMP/BLAS 线程并发，降低多原生库同进程时的线程争用（hygiene）
env.setdefault("OMP_NUM_THREADS", "1")
env.setdefault("OPENBLAS_NUM_THREADS", "1")
env.setdefault("MKL_NUM_THREADS", "1")
env.setdefault("TQDM_DISABLE", "1")

PY = sys.executable


def run(args, label):
    print(f"\n=== {label} ===", flush=True)
    rc = subprocess.call(
        [PY, "-m", "pytest", *args, "-q", "--tb=short", "-p", "no:cacheprovider"],
        cwd=str(ROOT), env=env,
    )
    print(f"{label} -> exit={rc}", flush=True)
    return rc


def main():
    # PART1：除 AppTest 外全部测试（与原生库同进程，无 pyarrow 冲突）
    rc1 = run(["tests", "--ignore=tests/test_ui_flows.py"], "PART1: 非 AppTest 测试")
    if rc1 != 0:
        return rc1
    # PART2：AppTest 测试单独进程，规避 pyarrow + torch/onnxruntime 原生崩溃
    return run(["tests/test_ui_flows.py"], "PART2: AppTest 测试（独立进程）")


if __name__ == "__main__":
    sys.exit(main())