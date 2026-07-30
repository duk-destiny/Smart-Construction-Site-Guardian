"""数据库连接与初始化：WAL 模式 + 外键开启 + 执行 schema。

统一经本模块获取连接，保证 journal_mode=WAL、foreign_keys=ON（DB 文档 §1/§7）。
"""
from __future__ import annotations

import os
import sqlite3


def get_conn(db_path: str = "data/app.db") -> sqlite3.Connection:
    """建立 SQLite 连接并开启 WAL 与外键约束；自动创建父目录。

    统一设置 row_factory=sqlite3.Row，使查询行支持列名访问（row["id"]）
    与位置访问（row[0]）兼容（DB 文档 §1/§7）。
    """
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """执行 schema.sql（建表/索引/触发器/视图）。"""
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    with open(schema_path, encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.commit()
