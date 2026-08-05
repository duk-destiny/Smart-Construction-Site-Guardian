"""把已确认的人工纠偏反馈样本导出为场景级候选训练数据。

输出：
- data/feedback_training/feedback_candidates.csv
- data/feedback_training/yolo/ppe/ 和 data/feedback_training/yolo/fire/
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.feedback_dataset import scene_for_detections, write_feedback_dataset  # noqa: E402
from dao.db import get_conn, init_db  # noqa: E402
from services.task_service import TaskService  # noqa: E402


def main() -> int:
    conn = get_conn()
    init_db(conn)
    samples = TaskService(conn).list_feedback_samples(limit=5000)
    yolo_root = ROOT / "data" / "feedback_training" / "yolo"
    summary = write_feedback_dataset(samples, yolo_root)

    rows = []
    for s in samples:
        if s["status"] != "confirmed":
            continue
        image_path = s["image_path"]
        if not image_path or not Path(image_path).exists():
            continue
        try:
            detections = json.loads(s["detection_json"] or "[]")
        except ValueError:
            detections = []
        scene = scene_for_detections(detections)
        if scene is None:
            continue
        rows.append({
            "created_at": s["created_at"],
            "task_id": s["task_id"],
            "image_path": image_path,
            "scene": scene,
            "auto_risk_level": s["auto_risk_level"],
            "corrected_risk_level": s["corrected_risk_level"],
            "reason": s["reason"],
            "detections": s["detection_json"],
            "corrected_labels": s["corrected_labels_json"],
        })

    out_dir = ROOT / "data" / "feedback_training"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "feedback_candidates.csv"
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [
            "created_at", "task_id", "image_path", "scene", "auto_risk_level",
            "corrected_risk_level", "reason", "detections", "corrected_labels",
        ])
        writer.writeheader()
        writer.writerows(rows)
    print(f"已生成候选训练数据: {path}")
    skipped = sum(summary["skipped"].values()) + sum(
        c["skipped"] for c in summary["scenes"].values())
    print(f"有效样本: {len(rows)}，无效/未确认样本跳过: {skipped}")
    for scene, counts in summary["scenes"].items():
        print(f"  {scene}: train={counts['train']} val={counts['val']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
