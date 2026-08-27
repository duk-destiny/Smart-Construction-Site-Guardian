"""逾期工单巡检 CLI（v0.2 生产驱动入口）。

演示环境不用本脚本——管理端「扫描逾期并催办」按钮 + 时间游标即可；
生产部署由系统 cron 定时调用（示例：每小时）::

    0 * * * * cd /path/to/hzz-fire-safety && .venv313/Scripts/python.exe scripts/overdue_scan.py

与 Web 进程完全解耦：同一 scan_overdue 纯函数，结果写 audit_logs
（overdue_notify / overdue_escalate），stdout 输出 JSON 汇总供运维采集。

用法：
    python scripts/overdue_scan.py                       # 以当前 UTC 时刻巡检
    python scripts/overdue_scan.py --as-of "2025-09-01 08:00:00" --db data/app.db
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dao.db import get_conn, init_db                      # noqa: E402
from services.dispatch_service import DispatchService     # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="逾期工单催办巡检")
    parser.add_argument("--as-of", default=None,
                        help="巡检时刻 'YYYY-MM-DD HH:MM:SS'（UTC）；省略=当前时刻")
    parser.add_argument("--escalate-after-hours", type=float, default=24.0,
                        help="逾期超过该小时数则追加越级升级审计（默认 24）")
    parser.add_argument("--db", default="data/app.db",
                        help="SQLite 路径（默认 data/app.db）")
    args = parser.parse_args()

    conn = get_conn(args.db)
    init_db(conn)
    try:
        result = DispatchService(conn).scan_overdue(
            as_of=args.as_of, escalate_after_hours=args.escalate_after_hours)
        print(json.dumps(result, ensure_ascii=False))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
