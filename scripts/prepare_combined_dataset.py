"""把多个 Roboflow/YOLO 数据集合并成项目统一训练集。

输出：
  data/combined/ppe/   classes: helmet, no_helmet, no_vest, person, vest
  data/combined/fire/  classes: spark, smoke, extinguisher

图片尽量用硬链接，标注重新映射后写入新文件，不改动 data/raw 原始数据。
"""
from __future__ import annotations

import os
import shutil
import sys
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "combined"

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

SPECS = [
    {
        "scene": "ppe",
        "src": "ppe_dataset",
        "prefix": "ppe_",
        "class_map": {0: 0, 1: 1, 2: 2, 3: 3, 4: 4},
    },
    {
        "scene": "ppe",
        "src": "helmet",
        "prefix": "helmet_",
        "class_map": {0: 0, 1: 1},
    },
    {
        "scene": "ppe",
        "src": "safety vest",
        "prefix": "vest_",
        "class_map": {0: 2, 1: 4},
    },
    {
        "scene": "ppe",
        "src": "construction-ppe",
        "prefix": "cppe_",
        "class_map": {0: 0, 2: 4, 6: 3, 7: 1},
    },
    {
        "scene": "ppe",
        "src": "security",
        "prefix": "sec_",
        "class_map": {1: 1},
    },
    {
        "scene": "ppe",
        "src": "person",
        "prefix": "person_",
        "class_map": {0: 3},
    },
    {
        "scene": "fire",
        "src": "YOLOv8-Fire-and-Smoke-Detection-main/datasets/fire-8",
        "prefix": "fire_",
        "class_map": {0: 0, 1: None, 2: 1},
    },
    {
        "scene": "fire",
        "src": "spark",
        "prefix": "spark_",
        "class_map": {0: 0, 1: 1, 2: 0},
    },
    {
        "scene": "fire",
        "src": "fire extinguishers",
        "prefix": "ext_",
        "class_map": {0: 2, 1: 2, 2: 2, 3: 2},
    },
    # —— 人工纠偏样本（仅已确认 confirmed 的才进 data/feedback_training）——
    # 接线闭环：改判→审核→build_feedback_dataset→此处并入 combined/train
    # class_map identity：纠偏标注已是项目统一类别，无需重映射
    {
        "scene": "ppe",
        "src": "feedback_training/yolo/ppe",
        "src_root": str(ROOT / "data" / "feedback_training" / "yolo" / "ppe"),
        "prefix": "fb_ppe_",
        "class_map": {0: 0, 1: 1, 2: 2, 3: 3, 4: 4},
    },
    {
        "scene": "fire",
        "src": "feedback_training/yolo/fire",
        "src_root": str(ROOT / "data" / "feedback_training" / "yolo" / "fire"),
        "prefix": "fb_fire_",
        "class_map": {0: 0, 1: 1, 2: 2},
    },
]

SCENE_NAMES = {
    "ppe": ["helmet", "no_helmet", "no_vest", "person", "vest"],
    "fire": ["spark", "smoke", "extinguisher"],
}


def _link_or_copy(src: Path, dst: Path) -> None:
    """优先硬链接，失败时退回复制；避免大目录重复占空间。"""
    if dst.exists():
        return
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def _transform_label(label_path: Path, class_map: dict[int, int | None],
                     stats: Counter) -> list[str]:
    """把源类别 id 映射到统一类别 id，返回 YOLO 标注行。"""
    lines: list[str] = []
    if not label_path.exists():
        stats["missing_label"] += 1
        return lines
    with open(label_path, encoding="utf-8", errors="replace") as f:
        for raw in f.read().splitlines():
            parts = raw.split()
            if not parts:
                continue
            try:
                src_cls = int(parts[0])
                vals = [float(v) for v in parts[1:5]]
            except ValueError:
                stats["invalid_line"] += 1
                continue
            mapped = class_map.get(src_cls)
            if mapped is None:
                stats["ignored_class"] += 1
                continue
            boxes = _label_boxes(parts)
            if not boxes:
                stats["invalid_box"] += 1
                continue
            for box in boxes:
                cx, cy, w, h = box
                if w <= 0.0 or h <= 0.0:
                    stats["zero_area"] += 1
                    continue
                lines.append(f"{mapped} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}")
    return lines


def _label_boxes(parts: list[str]) -> list[list[float]]:
    """兼容 YOLO bbox 与分割多边形标注，多边形转最小外接框。"""
    try:
        vals = [float(v) for v in parts[1:]]
    except ValueError:
        return []
    if len(vals) == 4:
        if any(v < 0.0 or v > 1.0 for v in vals):
            return []
        return [[vals[0], vals[1], vals[2], vals[3]]]
    if len(vals) >= 6 and len(vals) % 2 == 0:
        xs = vals[0::2]
        ys = vals[1::2]
        if any(v < 0.0 or v > 1.0 for v in xs + ys):
            return []
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        return [[
            (min_x + max_x) / 2,
            (min_y + max_y) / 2,
            max_x - min_x,
            max_y - min_y,
        ]]
    return []


def _prepare_spec(spec: dict) -> dict:
    scene = spec["scene"]
    src_root = Path(spec["src_root"]) if spec.get("src_root") else RAW / spec["src"]
    prefix = spec["prefix"]
    class_map = spec["class_map"]
    stats: Counter = Counter()

    processed = 0
    for split in ("train", "valid", "test"):
        # feedback 目录用 val 而非 valid，映射进来
        alt_split = "val" if split == "valid" else split
        image_dir = next(
            (p for p in (src_root / split / "images",
                         src_root / "images" / split,
                         src_root / alt_split / "images",
                         src_root / "images" / alt_split) if p.is_dir()),
            None)
        label_dir = next(
            (p for p in (src_root / split / "labels",
                         src_root / "labels" / split,
                         src_root / alt_split / "labels",
                         src_root / "labels" / alt_split) if p.is_dir()),
            None)
        if image_dir is None or label_dir is None:
            continue
        for img_path in sorted(image_dir.iterdir()):
            if img_path.suffix.lower() not in IMAGE_EXTS:
                continue
            label_path = label_dir / f"{img_path.stem}.txt"

            dst_image_dir = OUT / scene / split / "images"
            dst_label_dir = OUT / scene / split / "labels"
            dst_img = dst_image_dir / f"{prefix}{img_path.name}"
            dst_lab = dst_label_dir / f"{prefix}{img_path.stem}.txt"
            if dst_img.exists() and dst_lab.exists():
                stats["already_exists"] += 1
                continue

            lines = _transform_label(label_path, class_map, stats)
            if not lines and not label_path.exists():
                stats["images_skipped"] += 1
                continue

            dst_image_dir.mkdir(parents=True, exist_ok=True)
            dst_label_dir.mkdir(parents=True, exist_ok=True)

            _link_or_copy(img_path, dst_img)
            dst_lab.write_text("\n".join(lines), encoding="utf-8")
            stats["images"] += 1
            stats["labels"] += len(lines)
            for line in lines:
                stats[f"cls_{line.split()[0]}"] += 1
            processed += 1
            if processed % 5000 == 0:
                print(f"[{spec['src']}] 已处理 {processed} 张图片", flush=True)

    return {
        "scene": scene,
        "source": spec["src"],
        "stats": dict(stats),
    }


def _write_data_yaml(scene: str) -> None:
    scene_dir = OUT / scene
    data = {
        "path": str(scene_dir.resolve()).replace("\\", "/"),
        "train": "train/images",
        "val": "valid/images",
        "test": "test/images",
        "nc": len(SCENE_NAMES[scene]),
        "names": SCENE_NAMES[scene],
    }
    (scene_dir / "data.yaml").write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8")


def main() -> int:
    if not RAW.is_dir():
        print(f"缺少 data/raw: {RAW}")
        return 1

    OUT.mkdir(parents=True, exist_ok=True)

    summaries = [_prepare_spec(spec) for spec in SPECS]
    for scene in SCENE_NAMES:
        _write_data_yaml(scene)

    for summary in summaries:
        print(f"== {summary['source']} -> {summary['scene']} ==")
        print(summary["stats"])

    print("\n输出目录:")
    for scene in SCENE_NAMES:
        print(f"  {OUT / scene}")
        print(f"  {OUT / scene / 'data.yaml'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
