#!/usr/bin/env python3
"""审计日志归档与留存维护（v0.8，cron 生产入口）。

三档用法（默认只导出不删除——审计仅追加的 C4 语义默认不破）：

1. 只导出快照：    python scripts/audit_maintenance.py --before 2026-08-01
2. 按留存期导出：  python scripts/audit_maintenance.py --retention-days 365
3. 导出并删档：    python scripts/audit_maintenance.py --retention-days 365 --delete

--delete 的受控删除路径：导出文件行数与库内一致才执行；临时摘除
trg_audit_no_delete 触发器 → DELETE → 原样重建触发器；删除前先写一条
audit_archive 审计（含行数与归档文件名）作为不可篡改的 purge 凭证。
输出 JSON 摘要（exported/deleted/archive_file），供采集侧解析。
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dao.db import get_conn, init_db  # noqa: E402

_CSV_HEADER = ["id", "created_at", "user_id", "username", "action", "detail_json"]
_DELETE_TRIGGER_SQL = """
CREATE TRIGGER IF NOT EXISTS trg_audit_no_delete
BEFORE DELETE ON audit_logs
BEGIN
    SELECT RAISE(ABORT, 'audit_logs is append-only: DELETE denied');
END;
"""


def _export(conn: sqlite3.Connection, out_dir: Path, before: str) -> tuple[Path, int]:
    """把 created_at < before 的审计行导出为 CSV，返回 (文件路径, 行数)。"""
    rows = conn.execute(
        "SELECT a.id, a.created_at, a.user_id, u.username, a.action, a.detail_json "
        "FROM audit_logs a LEFT JOIN users u ON u.id = a.user_id "
        "WHERE a.created_at < ? ORDER BY a.id ASC", (before,)).fetchall()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"audit_before_{before.replace('-', '')}.csv"
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(_CSV_HEADER)
        for r in rows:
            writer.writerow([r["id"], r["created_at"], r["user_id"] or "",
                             r["username"] or "", r["action"],
                             r["detail_json"] or ""])
    return path, len(rows)


def purge(conn: sqlite3.Connection, out_dir: Path, before: str,
          delete: bool = False) -> dict:
    """导出 created_at < before 的审计行；delete=True 时校验后受控删除。

    删除路径：先写 audit_archive 审计凭证 → 摘除禁删触发器 → DELETE →
    重建触发器 → commit。导出行数与删除行数不一致则中止（不删除）。
    """
    archive_path, exported = _export(conn, out_dir, before)
    result = {"before": before, "archive_file": str(archive_path),
              "exported": exported, "deleted": 0}
    if not delete:
        return result

    pending = conn.execute(
        "SELECT COUNT(*) FROM audit_logs WHERE created_at < ?",
        (before,)).fetchone()[0]
    if pending != exported:
        result["error"] = f"导出行数 {exported} 与库内待删行数 {pending} 不一致，已中止删除"
        return result

    # 删除前先落 purge 凭证（audit 仅追加，事后可追溯是谁在何时删了多少）
    conn.execute(
        "INSERT INTO audit_logs(user_id, action, detail_json, created_at) "
        "VALUES(?, 'audit_archive', ?, datetime('now'))",
        (None, json.dumps({"before": before, "rows": int(exported),
                           "archive_file": str(archive_path)},
                          ensure_ascii=False)))
    conn.commit()
    conn.execute("DROP TRIGGER IF EXISTS trg_audit_no_delete")
    try:
        cur = conn.execute("DELETE FROM audit_logs WHERE created_at < ?", (before,))
        result["deleted"] = cur.rowcount
    finally:
        conn.executescript(_DELETE_TRIGGER_SQL)
    conn.commit()
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="审计日志归档/留存维护")
    ap.add_argument("--db", default=str(ROOT / "data" / "app.db"))
    ap.add_argument("--out", default=str(ROOT / "data" / "audit_archive"))
    ap.add_argument("--before", default=None,
                    help="归档 created_at 早于该日（YYYY-MM-DD，不含当日）的行")
    ap.add_argument("--retention-days", type=int, default=None,
                    help="按留存期计算 before=今天-N 天（与 --before 二选一）")
    ap.add_argument("--delete", action="store_true",
                    help="导出校验一致后删除已归档行（默认只导出不删除）")
    args = ap.parse_args()

    if args.before and args.retention_days is not None:
        print(json.dumps({"error": "--before 与 --retention-days 只能二选一"},
                         ensure_ascii=False))
        return 2
    if args.before:
        before = args.before
    elif args.retention_days is not None:
        before = (datetime.utcnow() - timedelta(days=args.retention_days)) \
            .strftime("%Y-%m-%d")
    else:
        print(json.dumps({"error": "必须指定 --before 或 --retention-days"},
                         ensure_ascii=False))
        return 2

    conn = get_conn(args.db)
    try:
        init_db(conn)
        result = purge(conn, Path(args.out), before, delete=args.delete)
    finally:
        conn.close()
    print(json.dumps(result, ensure_ascii=False))
    return 1 if "error" in result else 0


if __name__ == "__main__":
    raise SystemExit(main())
