"""UI→services 连接收口（Phase 0）：UI 层不得出现 get_conn/init_db。

`scoped()` 上下文管理器统一"打开 → init_db → 使用 → 关闭"生命周期，
异常安全；服务门面模块（*_service / *_entry）用它自持连接，
UI 只调服务函数。dao 仍是唯一 SQL 层。
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Callable, TypeVar

from dao.db import get_conn, init_db

T = TypeVar("T")


@contextmanager
def scoped(db_path: str | None = None):
    """打开并初始化一个 SQLite 连接，退出时确保关闭（含异常路径）。"""
    conn = get_conn(db_path) if db_path else get_conn()
    try:
        init_db(conn)
        yield conn
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001 关闭失败不应掩盖业务异常
            pass


def call(fn: Callable[[sqlite3.Connection], T], db_path: str | None = None) -> T:
    """在托管连接上执行 fn(conn) 并返回结果（一次性读写场景）。"""
    with scoped(db_path) as conn:
        return fn(conn)
