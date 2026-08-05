"""训练守护：只记录最佳 mAP，训练结束后写 final_best.json。"""
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
FINAL_BEST = ROOT / "data" / "train" / "final_best.json"
GUARD_LOG = ROOT / "data" / "train" / "guard.log"


def _log(message: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    print(line, flush=True)
    GUARD_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(GUARD_LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


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


def _read_rows(run_dir: Path) -> list[dict]:
    results = run_dir / "results.csv"
    if not results.exists():
        return []
    try:
        with open(results, encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))
    except (OSError, csv.Error):
        return []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--poll-seconds", type=int, default=10)
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    best: dict = {}
    seen_epochs: set[int] = set()
    _log(f"GUARD_START run_dir={run_dir} pid={args.pid}")

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
            if epoch not in seen_epochs:
                seen_epochs.add(epoch)
                if not best or map50 > best["mAP50"]:
                    best = {
                        "epoch": epoch,
                        "mAP50": round(map50, 6),
                        "mAP50_95": round(map50_95, 6),
                    }
                    _log(f"NEW_BEST epoch={epoch} mAP50={map50:.6f} "
                         f"mAP50-95={map50_95:.6f}")
                else:
                    _log(f"EPOCH {epoch} mAP50={map50:.6f} "
                         f"mAP50-95={map50_95:.6f}")
        time.sleep(args.poll_seconds)

    final = {"ok": True, "best": best}
    FINAL_BEST.parent.mkdir(parents=True, exist_ok=True)
    FINAL_BEST.write_text(
        json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(f"GUARD_EXIT best={best}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
