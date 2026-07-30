"""初始化：建库 + 内置 admin + 初始化审计。

向量库构建（RagEngine.build）在 RAG 引擎 Task 10 就绪后由管理页触发，
此处不阻塞（DB 文档 §7 第 3 点：首次向量库可选）。
"""
from __future__ import annotations

import bcrypt

from dao.db import get_conn, init_db
from dao.models import AuditDAO, UserDAO

DEFAULT_ADMIN_USER = "admin"
DEFAULT_ADMIN_PWD = "admin@2026"


def main() -> None:
    conn = get_conn()
    init_db(conn)
    users = UserDAO(conn)
    if users.get_by_name(DEFAULT_ADMIN_USER) is None:
        pwd_hash = bcrypt.hashpw(DEFAULT_ADMIN_PWD.encode(), bcrypt.gensalt()).decode()
        users.insert(DEFAULT_ADMIN_USER, pwd_hash, "admin")
    # 初始化审计仅首次写入（仍为 INSERT，不违反仅追加约束 C4）
    if conn.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0] == 0:
        AuditDAO(conn).insert(None, "init", '{"detail":"系统初始化"}')
    conn.close()
    print(f"初始化完成：库已建，内置账号 {DEFAULT_ADMIN_USER} / {DEFAULT_ADMIN_PWD}")


if __name__ == "__main__":
    main()
