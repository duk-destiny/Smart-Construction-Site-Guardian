"""导出人工纠偏反馈样本为 CSV，供后续训练与规则修订使用。"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dao.db import get_conn, init_db  # noqa: E402
from services.task_service import TaskService  # noqa: E402


def main() -> int:
    conn = get_conn()
    init_db(conn)
    csv_text = TaskService(conn).feedback_csv()
    out_dir = ROOT / "data" / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"feedback_samples_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    path.write_text(csv_text, encoding="utf-8-sig")
    print(f"已导出: {path}")
    print(f"行数: {csv_text.count(chr(10)) - 1}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
