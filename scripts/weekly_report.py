"""风险周报生成 CLI（v0.3 生产驱动入口）。

演示环境用管理端「生成周报」按钮即可；生产部署由系统 cron 定时归档
（示例：每周一 08:00 生成上周报告）::

    0 8 * * 1 cd /path/to/hzz-fire-safety && .venv313/Scripts/python.exe scripts/weekly_report.py

stdout 输出 JSON 摘要（文件路径+核心指标）供运维采集；文件落 data/exports/。

用法：
    python scripts/weekly_report.py --days 7                 # 近7天（默认）
    python scripts/weekly_report.py --start 2025-09-01 --end 2025-09-07
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dao.db import get_conn, init_db                     # noqa: E402
from services.report_service import WeeklyReportService  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="生成风险分级周报 PDF")
    parser.add_argument("--days", type=int, default=7,
                        help="从今天往前回看的周期天数（默认 7）")
    parser.add_argument("--start", default=None, help="起始日期 YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="结束日期 YYYY-MM-DD")
    parser.add_argument("--out-dir", default="data/exports",
                        help="输出目录（默认 data/exports）")
    parser.add_argument("--db", default="data/app.db", help="SQLite 路径")
    args = parser.parse_args()

    end = args.end or date.today().isoformat()
    start = args.start or (date.fromisoformat(end) - timedelta(days=args.days - 1)) \
        .isoformat()

    conn = get_conn(args.db)
    init_db(conn)
    try:
        result = WeeklyReportService(conn).generate(start, end, user_id=None,
                                                    out_dir=args.out_dir)
        stats = result["data"]["stats"]
        print(json.dumps({
            "ok": True, "file": result["data"]["file_path"],
            "period": [stats["start"], stats["end"]],
            "frames": stats["frames"], "bad": stats["bad"],
            "orders_total": stats["orders_total"],
            "overdue_open_now": stats["overdue_open_now"],
        }, ensure_ascii=False))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
