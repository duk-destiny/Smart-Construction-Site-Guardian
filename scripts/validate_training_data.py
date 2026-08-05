"""训练数据校验：在正式训练前检查 YOLO 格式、类别、图片标签对应关系。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _validate_split(images: Path, labels: Path, names: list[str]) -> tuple[int, list[str]]:
    if not images.is_dir() or not labels.is_dir():
        return 0, [f"缺少 images/labels 目录: {images} / {labels}"]
    errors: list[str] = []
    image_files = [p for p in images.iterdir()
                   if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp", ".bmp")]
    label_map = {p.stem: p for p in labels.glob("*.txt")}
    for img in image_files:
        lab = label_map.get(img.stem)
        if lab is None:
            errors.append(f"图片缺少标签: {img.name}")
            continue
        for line in lab.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.split()
            if len(parts) != 5:
                errors.append(f"{lab.name} 非法行: {line}")
                continue
            try:
                cls = int(parts[0])
                vals = [float(v) for v in parts[1:]]
            except ValueError:
                errors.append(f"{lab.name} 非数字: {line}")
                continue
            if cls >= len(names):
                errors.append(f"{lab.name} 类别越界: {cls}")
            if not all(0 <= v <= 1 for v in vals) or vals[2] <= 0 or vals[3] <= 0:
                errors.append(f"{lab.name} bbox 非法: {line}")
    return len(image_files), errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True, help="data.yaml 路径")
    args = parser.parse_args()
    data_path = Path(args.data)
    if not data_path.exists():
        print(f"data.yaml 不存在: {data_path}")
        return 1
    data = yaml.safe_load(data_path.read_text(encoding="utf-8")) or {}
    names = data.get("names") or []
    if not names or data.get("nc") != len(names):
        print("names 与 nc 不一致")
        return 1
    all_errors = []
    total_images = 0
    base = data_path.parent
    for split in ("train", "valid", "test"):
        rel = data.get(split) or (data.get("val") if split == "valid" else "")
        images_dir = base / rel
        if not images_dir.is_dir():
            all_errors.append(f"缺少 {split} images 目录: {images_dir}")
            continue
        labels_dir = images_dir.parent / "labels"
        count, errors = _validate_split(images_dir, labels_dir, names)
        total_images += count
        all_errors.extend(errors)
    if all_errors:
        print("校验失败:")
        for err in all_errors[:50]:
            print(f" - {err}")
        return 1
    print(f"校验通过: 图片 {total_images} 张，类别 {len(names)} 个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
