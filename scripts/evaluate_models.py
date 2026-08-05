"""可复现的本地模型评测：在测试集上计算每个隐患类别的 Precision/Recall/F1。

用法：
  venv313/Scripts/python.exe scripts/evaluate_models.py

输出：
  - 终端 Markdown 表格
  - data/eval/model_eval.json（供 README / 答辩材料引用）
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.yolo_engine import YoloEngine

IOU_THRESHOLD = 0.5

MODEL_SPECS = {
    "fire": {
        "model": "data/models/yolov8_fire_smoke_v2.onnx",
        "test_dir": "data/raw/YOLOv8-Fire-and-Smoke-Detection-main/datasets/fire-8/test",
        "class_map": {
            "spark": "spark",
            "smoke": "smoke",
            "extinguisher": "extinguisher",
        },
        "gt_class_map": {0: "spark", 1: None, 2: "smoke"},
        "labels": {"spark": "火花", "smoke": "烟雾"},
    },
    "ppe": {
        "model": "data/models/ppe_yolov8_v2.onnx",
        "test_dir": "data/raw/ppe_dataset/test",
        "class_map": {
            "helmet": "helmet",
            "no_helmet": "no_helmet",
            "no_vest": "no_vest",
            "person": "person",
            "vest": "vest",
        },
        "gt_class_map": {
            0: "helmet", 1: "no_helmet", 2: "no_vest",
            3: "person", 4: "vest",
        },
        "labels": {
            "helmet": "佩戴安全帽", "no_helmet": "未佩戴安全帽",
            "no_vest": "未穿反光衣", "person": "人员", "vest": "穿着反光衣",
        },
    },
}


def _iou(a: list[float], b: list[float]) -> float:
    ax1, ay1 = a[0] - a[2] / 2, a[1] - a[3] / 2
    ax2, ay2 = a[0] + a[2] / 2, a[1] + a[3] / 2
    bx1, by1 = b[0] - b[2] / 2, b[1] - b[3] / 2
    bx2, by2 = b[0] + b[2] / 2, b[1] + b[3] / 2
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    union = ((ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter)
    return inter / max(union, 1e-9)


def _evaluate_at(spec: dict, conf: float) -> dict:
    engine = YoloEngine(conf_thres=conf, iou_thres=0.45,
                        class_map=spec["class_map"])
    engine.load(spec["model"])
    stats = collections.defaultdict(lambda: [0, 0, 0])  # tp, fp, fn
    images = sorted(glob.glob(os.path.join(spec["test_dir"], "images", "*")))
    if args_limit:
        rng = random.Random(0)
        rng.shuffle(images)
        images = images[:args_limit]
    total_images = 0
    images_with_detections = 0

    for img_path in images:
        base = os.path.splitext(os.path.basename(img_path))[0]
        label_path = os.path.join(spec["test_dir"], "labels", base + ".txt")
        if not os.path.isfile(label_path):
            continue
        total_images += 1

        gts: list[tuple[str, list[float]]] = []
        with open(label_path, encoding="utf-8", errors="replace") as f:
            for line in f.read().splitlines():
                parts = line.split()
                if not parts:
                    continue
                cls = spec["gt_class_map"].get(int(parts[0]))
                if cls is None:
                    continue
                gts.append((cls, [float(v) for v in parts[1:5]]))

        dets: list[tuple[str, float, list[float]]] = []
        for d in engine.infer(img_path):
            if d["conf"] < conf:
                continue
            cx, cy, w, h = d["bbox"]
            norm = [
                cx / engine.input_size[1],
                cy / engine.input_size[0],
                w / engine.input_size[1],
                h / engine.input_size[0],
            ]
            dets.append((d["cls"], d["conf"], norm))
        if dets:
            images_with_detections += 1

        dets.sort(key=lambda x: -x[1])
        matched: set[int] = set()
        for cls, _conf, box in dets:
            if cls not in spec["labels"]:
                continue
            best_idx: int | None = None
            best_iou = IOU_THRESHOLD
            for idx, (gt_cls, gt_box) in enumerate(gts):
                if idx in matched or gt_cls != cls:
                    continue
                value = _iou(box, gt_box)
                if value > best_iou:
                    best_iou = value
                    best_idx = idx
            if best_idx is not None:
                matched.add(best_idx)
                stats[cls][0] += 1
            else:
                stats[cls][1] += 1
        for idx, (gt_cls, _gt_box) in enumerate(gts):
            if idx not in matched:
                stats[gt_cls][2] += 1

    rows = []
    for cls in sorted(spec["labels"]):
        tp, fp, fn = stats[cls]
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        rows.append({
            "class": cls,
            "label": spec["labels"][cls],
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
        })
    return {
        "conf_threshold": conf,
        "iou_threshold": IOU_THRESHOLD,
        "images": total_images,
        "images_with_detections": images_with_detections,
        "classes": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--thresholds", nargs="+", type=float,
                        default=[0.25, 0.45])
    parser.add_argument("--limit", type=int, default=0,
                        help="每个数据集最多评测的图片数，0 表示全部")
    parser.add_argument("--output", default="data/eval/model_eval.json")
    args = parser.parse_args()
    global args_limit
    args_limit = args.limit

    report: dict = {"iou_threshold": IOU_THRESHOLD, "models": {}}
    for name, spec in MODEL_SPECS.items():
        report["models"][name] = {
            "model": spec["model"],
            "test_dir": spec["test_dir"],
            "results": [_evaluate_at(spec, conf) for conf in args.thresholds],
        }
        print(f"## {name}")
        print("| 类别 | TP | FP | FN | Precision | Recall | F1 |")
        print("|---|---:|---:|---:|---:|---:|---:|")
        for result in report["models"][name]["results"]:
            for row in result["classes"]:
                print(
                    f"| {row['label']} (conf {result['conf_threshold']:.2f}) "
                    f"| {row['tp']} | {row['fp']} | {row['fn']} "
                    f"| {row['precision']:.2f} | {row['recall']:.2f} | {row['f1']:.2f} |"
                )
        print()

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"已写入: {output_path}")


if __name__ == "__main__":
    main()
