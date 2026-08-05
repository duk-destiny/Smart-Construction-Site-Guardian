"""把已确认反馈样本生成场景级 YOLO 训练集。

输出：
  data/feedback_training/yolo/ppe/
  data/feedback_training/yolo/fire/

用法：
  venv313/Scripts/python.exe scripts/prepare_feedback_training.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.feedback_dataset import write_feedback_dataset  # noqa: E402
from dao.db import get_conn, init_db  # noqa: E402
from services.task_service import TaskService  # noqa: E402


def main() -> int:
    conn = get_conn()
    init_db(conn)
    samples = TaskService(conn).list_feedback_samples(limit=5000)
    out_dir = ROOT / "data" / "feedback_training" / "yolo"
    summary = write_feedback_dataset(samples, out_dir)
    print(f"输出目录: {out_dir}")
    print("场景统计:")
    for scene, counts in summary["scenes"].items():
        print(f"  {scene}: train={counts['train']} val={counts['val']} skipped={counts['skipped']}")
    print(f"跳过统计: {summary['skipped']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
