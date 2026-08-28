"""训练守护脚本：监控 results.csv，按 mAP 停滞或过低自动早停。"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASK_FILE = ROOT / "data" / "train" / "training_task.json"
WATCH_LOG = ROOT / "data" / "train" / "watch.log"


def _log(message: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    print(line, flush=True)
    WATCH_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(WATCH_LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _read_rows(run_dir: Path) -> list[dict]:
    results = run_dir / "results.csv"
    if not results.exists():
        return []
    try:
        with open(results, encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))
    except (OSError, csv.Error):
        return []


def _alive(pid: int) -> bool:
    if os.name == "nt":
        try:
            result = subprocess.run(
                [
                    "powershell", "-NoProfile", "-Command",
                    f"if (Get-Process -Id {int(pid)} -ErrorAction SilentlyContinue) {{ 'alive' }} else {{ 'dead' }}",
                ],
                capture_output=True, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW, timeout=5)
            return "alive" in result.stdout
        except (OSError, subprocess.SubprocessError):
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _stop(pid: int, reason: str) -> None:
    _log(f"EARLY_STOP: {reason}")
    if _alive(pid):
        # 安全审查（S8705）: pid 为 int 类型，列表参数无 shell=True，无注入风险。
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        task = json.loads(TASK_FILE.read_text(encoding="utf-8"))
        task["phase"] = "early_stopped"
        task["message"] = reason
        # 安全审查（S2083）: TASK_FILE 为常量路径，非用户可控。
        TASK_FILE.write_text(
            json.dumps(task, ensure_ascii=False, indent=2),
            encoding="utf-8")
    except (OSError, json.JSONDecodeError):
        pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--min-map50", type=float, default=0.55)
    parser.add_argument("--stall-epochs", type=int, default=5)
    parser.add_argument("--poll-seconds", type=int, default=5)
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    history: list[tuple[int, float]] = []
    best_map = -1.0
    _log(
        f"WATCHER_START run_dir={run_dir} pid={args.pid} "
        f"min_map50={args.min_map50} stall_epochs={args.stall_epochs}"
    )

    while _alive(args.pid):
        rows = _read_rows(run_dir)
        if rows:
            row = rows[-1]
            try:
                epoch = int(float(row["epoch"]))
                map50 = float(row["metrics/mAP50(B)"])
                map50_95 = float(row["metrics/mAP50-95(B)"])
            except (KeyError, TypeError, ValueError):
                time.sleep(args.poll_seconds)
                continue

            if not history or history[-1][0] != epoch:
                history.append((epoch, map50))
                #  stall 检测：比较最近 N 轮与**进入该窗口前**的最佳值
                #  （不能用含当前 epoch 的 best_map，否则 max(recent)<=best_map 恒成立）
                best_before_window = max((m for e, m in history[:-args.stall_epochs]), default=0.0)
                best_map = max(best_map, map50)
                _log(
                    f"EPOCH {epoch} mAP50={map50:.4f} "
                    f"mAP50-95={map50_95:.4f} best={best_map:.4f}"
                )

                if epoch > 2 and map50 < args.min_map50:
                    _stop(args.pid, (
                        f"epoch {epoch} mAP50 {map50:.4f} < "
                        f"{args.min_map50}"))
                    break

                recent = [m for e, m in history if e >= epoch - args.stall_epochs + 1]
                if len(recent) >= args.stall_epochs:
                    if max(recent) <= best_before_window:
                        _stop(args.pid, (
                            f"last {args.stall_epochs} epochs no improvement, "
                            f"best={best_before_window:.4f}"))
                        break
        time.sleep(args.poll_seconds)

    _log("WATCHER_EXIT")
    return 0


if __name__ == "__main__":
    sys.exit(main())
