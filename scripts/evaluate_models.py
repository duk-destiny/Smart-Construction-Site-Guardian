"""可复现的本地模型评测：在测试集上计算每个隐患类别的 Precision/Recall/F1。

评测口径：独立测试集逐类 P/R/F1（与 model_registry 的训练验证集 mAP 互补）。
模型路径从 DB model_registry 读取（不再硬编码），按版本聚合写入 JSON。

用法：
  # 评测所有已注册版本（默认）
  python scripts/evaluate_models.py
  # 只评测指定版本
  python scripts/evaluate_models.py --version v3
  # 指定阈值
  python scripts/evaluate_models.py --thresholds 0.25 0.30 0.35 0.45

新增模型后的流程：
  1. 复训完自动 register 进 model_registry（训练集 mAP，已有逻辑）
  2. 跑一次本脚本（--version <新版本号> 或默认 --all）→ 在独立测试集评测
  3. 结果 merge 进 model_eval.json（不覆盖已有版本），UI 自动展示

输出：
  - 终端 Markdown 表格
  - data/eval/model_eval.json（供 README / 答辩材料引用，按 scene+version 聚合）
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import random
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.yolo_engine import YoloEngine

IOU_THRESHOLD = 0.5

# 场景级配置（测试集路径、类名映射与版本无关，固定）
SCENE_SPECS = {
    "fire": {
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
            "helmet": "佩戴安全帽", "no_helmet": "未戴安全帽",
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


def _load_registry() -> dict:
    """从 model_registry 读取所有已注册版本，按 scene 分组返回。"""
    db_path = ROOT / "data" / "app.db"
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT name, version, path FROM model_registry "
            "ORDER BY name, created_at"
        ).fetchall()
    finally:
        conn.close()
    out: dict = {}
    for r in rows:
        out.setdefault(r["name"], []).append(
            {"version": r["version"], "path": r["path"]})
    return out


def _evaluate_at(spec: dict, model_path: str, conf: float,
                 limit: int) -> dict:
    engine = YoloEngine(conf_thres=conf, iou_thres=0.45,
                        class_map=spec["class_map"])
    engine.load(model_path)
    stats = collections.defaultdict(lambda: [0, 0, 0])  # tp, fp, fn
    images = sorted(glob.glob(os.path.join(spec["test_dir"], "images", "*")))
    if limit:
        # 安全审查（S2245）: 可复现评测洗牌，非安全随机数场景。
        rng = random.Random(0)
        rng.shuffle(images)
        images = images[:limit]
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


def _migrate_old(report: dict) -> dict:
    """旧结构 models.<scene> = {model, test_dir, results} → 按版本聚合。

    旧文件无版本层，把直接挂在 scene 下的结果迁移到版本子键。
    model 文件名含 _vN，提取版本号；提取失败则记 _legacy。
    """
    models = report.get("models") or {}
    for scene, data in list(models.items()):
        if not isinstance(data, dict):
            continue
        # 已是新结构（值里含 version 子键）则跳过
        if "results" in data and isinstance(data.get("results"), list):
            model_path = data.get("model", "")
            m = re.search(r"_v(\d+)\.", model_path)
            ver = "v" + m.group(1) if m else "_legacy"
            models[scene] = {ver: {
                "model": model_path,
                "test_dir": data.get("test_dir", ""),
                "results": data["results"],
            }}
    report["models"] = models
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--thresholds", nargs="+", type=float,
                        default=[0.25, 0.45])
    parser.add_argument("--limit", type=int, default=0,
                        help="每个数据集最多评测的图片数，0 表示全部")
    parser.add_argument("--output", default="data/eval/model_eval.json")
    parser.add_argument("--version", default=None,
                        help="只评测指定版本（如 v3）；省略则评测所有已注册版本")
    parser.add_argument("--scene", default=None,
                        help="只评测指定场景（fire/ppe）；省略则全部")
    args = parser.parse_args()

    registry = _load_registry()
    if not registry:
        print("[warn] model_registry 为空或 app.db 不存在，无版本可评测")
        return

    output_path = Path(args.output).resolve()
    # 安全审查（S2083）: 校验输出路径在项目根目录下，防路径穿越。
    _root = Path(__file__).resolve().parent.parent
    try:
        output_path.relative_to(_root)
    except ValueError:
        print(f"输出路径必须在项目根目录下: {output_path}", flush=True)
        return
    # 读取并迁移旧结构，保留已有版本结果（merge）
    report: dict = {"iou_threshold": IOU_THRESHOLD, "models": {}}
    if output_path.exists():
        try:
            report = json.loads(output_path.read_text(encoding="utf-8"))
            report = _migrate_old(report)
        except (json.JSONDecodeError, OSError):
            pass
    report.setdefault("models", {})

    scenes_to_eval = (
        [args.scene] if args.scene else list(SCENE_SPECS.keys()))
    eval_count = 0
    for scene in scenes_to_eval:
        spec = SCENE_SPECS.get(scene)
        if spec is None:
            print(f"[skip] 未知场景 {scene}")
            continue
        versions = registry.get(scene, [])
        if args.version:
            versions = [v for v in versions if v["version"] == args.version]
        if not versions:
            print(f"[skip] {scene} 无匹配的已注册版本"
                  + (f"（--version {args.version}）" if args.version else ""))
            continue

        report["models"].setdefault(scene, {})
        for vinfo in versions:
            ver = vinfo["version"]
            model_path = vinfo["path"]
            if not os.path.isfile(model_path):
                print(f"[skip] {scene}/{ver} 模型文件不存在: {model_path}")
                continue
            print(f"\n## {scene} / {ver}  ({model_path})")
            results = [
                _evaluate_at(spec, model_path, conf, args.limit)
                for conf in args.thresholds
            ]
            report["models"][scene][ver] = {
                "model": model_path,
                "test_dir": spec["test_dir"],
                "results": results,
            }
            eval_count += 1
            print("| 类别 | TP | FP | FN | Precision | Recall | F1 |")
            print("|---|---:|---:|---:|---:|---:|---:|")
            for result in results:
                for row in result["classes"]:
                    print(
                        f"| {row['label']} (conf {result['conf_threshold']:.2f}) "
                        f"| {row['tp']} | {row['fp']} | {row['fn']} "
                        f"| {row['precision']:.2f} | {row['recall']:.2f} | {row['f1']:.2f} |"
                    )

    if eval_count == 0:
        print("[done] 未评测任何版本")
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"\n已写入 {output_path}（共 {eval_count} 个版本，merge 保留已有）")


if __name__ == "__main__":
    main()
