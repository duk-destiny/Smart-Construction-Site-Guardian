"""早停后导出当前 best.pt 为 ONNX，并写训练结果 JSON。"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from pathlib import Path

import torch
from ultralytics import YOLO

ROOT = Path(__file__).resolve().parent.parent
MODEL_YAML = "yolov8s.yaml"
RESULT_FILE = ROOT / "data" / "train" / "last_training_result.json"


def _output_path(name: str, version: str) -> Path:
    if name == "ppe":
        return ROOT / "data" / "models" / f"ppe_yolov8_{version}.onnx"
    return ROOT / "data" / "models" / f"yolov8_fire_smoke_{version}.onnx"


def _best_metrics(run_dir: Path) -> dict:
    results = run_dir / "results.csv"
    if not results.exists():
        return {}
    try:
        with open(results, encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        if not rows:
            return {}
        best = max(rows, key=lambda r: float(r.get("metrics/mAP50(B)") or 0))
        return {
            "epoch": int(float(best["epoch"])),
            "mAP50": float(best["metrics/mAP50(B)"]),
            "mAP50_95": float(best.get("metrics/mAP50-95(B)") or 0),
        }
    except (KeyError, TypeError, ValueError):
        return {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    best_pt = run_dir / "weights" / "best.pt"
    if not best_pt.exists():
        print(f"BEST_PT_MISSING: {best_pt}", flush=True)
        return 1

    # 安全审查（S6985）: YOLOv8 checkpoint 含 ema/model 非张量对象，无法用 weights_only=True；
    # 本地训练脚本，权重由管理员自产可信，接受风险。
    ckpt = torch.load(str(best_pt), map_location="cpu", weights_only=False)
    src_model = ckpt.get("ema") or ckpt.get("model")
    if src_model is None:
        print(f"BEST_PT_NO_MODEL: {best_pt}", flush=True)
        return 1
    model = YOLO(MODEL_YAML)
    model.model = src_model
    model.model.task = "detect"
    if hasattr(model.model, "args") and not isinstance(model.model.args, dict):
        model.model.args = vars(model.model.args)
    model.ckpt = ckpt
    exported = model.export(format="onnx", imgsz=640, opset=17, simplify=False)

    dst = _output_path(args.name, args.version)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(exported, dst)
    if Path(exported).name == "yolov8s.onnx":
        try:
            Path(exported).unlink()
        except OSError:
            pass

    result = {
        "ok": True,
        "results": {
            args.name: {
                "name": args.name,
                "version": args.version,
                "path": str(dst.relative_to(ROOT)).replace("\\", "/"),
                "run_dir": str(run_dir),
                **_best_metrics(run_dir),
            }
        },
    }
    RESULT_FILE.parent.mkdir(parents=True, exist_ok=True)
    RESULT_FILE.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"TRAIN_RESULT_JSON={RESULT_FILE}", flush=True)
    print(f"EXPORT_OK={dst}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
