"""把已确认的人工纠偏反馈样本转换为场景级 YOLO 训练数据。"""
from __future__ import annotations

import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path

import yaml

SCENE_NAMES = {
    "ppe": ["helmet", "no_helmet", "no_vest", "person", "vest"],
    "fire": ["spark", "smoke", "extinguisher"],
}
SCENE_CLASSES = {scene: set(names) for scene, names in SCENE_NAMES.items()}


def scene_for_detections(detections: list[dict]) -> str | None:
    """根据检测类别推断场景；跨场景混合样本不自动拆分。"""
    scenes = {
        scene for scene, classes in SCENE_CLASSES.items()
        if any(d.get("cls") in classes for d in detections)
    }
    if len(scenes) == 1:
        return scenes.pop()
    return None


def feedback_yolo_rows(
    detections: list[dict],
    corrections: list[dict],
    scene: str,
) -> list[str]:
    """把检测框和人工修正映射为场景内 YOLO 标注行。"""
    rows: list[str] = []
    names = SCENE_NAMES.get(scene, [])
    allowed = SCENE_CLASSES.get(scene, set())
    for i, det in enumerate(detections):
        fix = corrections[i] if i < len(corrections) else {}
        if fix.get("is_fp"):
            continue
        cls = fix.get("corrected_cls") or det.get("cls")
        box = det.get("bbox")
        if cls not in allowed:
            continue
        if not isinstance(box, list) or len(box) != 4:
            continue
        if not all(isinstance(v, (int, float)) and 0 <= float(v) <= 1 for v in box):
            continue
        cx, cy, w, h = (float(v) for v in box)
        if w <= 0 or h <= 0:
            continue
        rows.append(f"{names.index(cls)} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}")
    return rows


def _load_json(value: str | None, default: list) -> list:
    if not value:
        return default
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else default
    except ValueError:
        return default


def write_feedback_dataset(
    samples: list[dict],
    output_dir: Path | str,
    train_ratio: float = 0.8,
) -> dict:
    """写场景级 YOLO 数据，返回 {scene: {train, val, skipped}}。"""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stats: dict[str, Counter] = {
        scene: Counter({"train": 0, "val": 0, "skipped": 0})
        for scene in SCENE_NAMES
    }
    total_skipped = Counter()

    for sample in samples:
        if sample.get("status") != "confirmed":
            total_skipped["not_confirmed"] += 1
            continue
        detections = _load_json(sample.get("detection_json"), [])
        scene = scene_for_detections(detections)
        if scene is None:
            total_skipped["unassigned_scene"] += 1
            continue
        image_path = sample.get("image_path")
        if not image_path:
            total_skipped["missing_image"] += 1
            continue
        src = Path(image_path)
        if not src.exists():
            src = Path.cwd() / image_path
        if not src.exists():
            total_skipped["missing_image"] += 1
            continue

        corrections = _load_json(sample.get("corrected_labels_json"), [])
        rows = feedback_yolo_rows(detections, corrections, scene)
        if not rows:
            stats[scene]["skipped"] += 1
            continue

        name = f"{scene}_{sample['task_id']}_{sample['id']}.jpg"
        digest = int(hashlib.sha256(name.encode("utf-8")).hexdigest(), 16)
        split = "train" if digest % 100 < int(train_ratio * 100) else "val"
        img_dir = out / scene / "images" / split
        lab_dir = out / scene / "labels" / split
        img_dir.mkdir(parents=True, exist_ok=True)
        lab_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, img_dir / name)
        (lab_dir / f"{Path(name).stem}.txt").write_text(
            "\n".join(rows), encoding="utf-8")
        stats[scene][split] += 1

    for scene, names in SCENE_NAMES.items():
        scene_dir = out / scene
        if not scene_dir.exists():
            continue
        val_rel = "val/images" if stats[scene]["val"] else "train/images"
        data = {
            "path": str(scene_dir.resolve()).replace("\\", "/"),
            "train": "train/images",
            "val": val_rel,
            "nc": len(names),
            "names": names,
        }
        (scene_dir / "data.yaml").write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8")

    return {
        "scenes": {scene: dict(c) for scene, c in stats.items()},
        "skipped": dict(total_skipped),
    }
