"""SQLite 在线备份脚本（使用 sqlite3 .backup() API，不锁库）。

用法：
    python -m scripts.backup_db                        # 备份到 data/backups/app_YYYYMMDD_HHMMSS.db
    python -m scripts.backup_db --dest D:/backup       # 指定目标目录
    python -m scripts.backup_db --db data/app.db       # 指定源库路径
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "app.db"
DEFAULT_DEST = ROOT / "data" / "backups"


def backup(src: str, dest_dir: str) -> Path:
    """执行在线备份，返回备份文件路径。"""
    os.makedirs(dest_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = Path(dest_dir) / f"app_{ts}.db"

    src_conn = sqlite3.connect(src)
    dst_conn = sqlite3.connect(str(dest))
    try:
        src_conn.backup(dst_conn)
    finally:
        src_conn.close()
        dst_conn.close()

    size_mb = dest.stat().st_size / (1024 * 1024)
    print(f"备份完成: {dest} ({size_mb:.1f} MB)")
    return dest


def main() -> None:
    parser = argparse.ArgumentParser(description="SQLite 在线备份")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="源数据库路径")
    parser.add_argument("--dest", default=str(DEFAULT_DEST), help="备份目标目录")
    args = parser.parse_args()

    if not os.path.exists(args.db):
        print(f"错误: 数据库不存在 {args.db}", file=sys.stderr)
        sys.exit(1)

    backup(args.db, args.dest)


if __name__ == "__main__":
    main()
