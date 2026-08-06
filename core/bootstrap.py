"""首次启动自举：建库 + 种子默认账号，保证 clone 后开箱即登录。

评委拉取仓库后首次 ``streamlit run app.py`` 即自动建表并写入两个默认账号；
已有账号时不做任何改动（幂等）。默认账号仅用于演示，上线前请修改密码。
"""
from __future__ import annotations

import bcrypt

from dao.db import get_conn, init_db

# 默认账号（演示用）：admin 全权限，safety 受限。
_DEFAULT_USERS = (
    # (username, password, role)
    ("admin", "admin123", "admin"),
    ("safety", "demo1234", "safety"),
)


def ensure_initialized() -> None:
    """建库；若 users 表为空则种子默认账号。幂等，安全可重复调用。"""
    conn = get_conn()
    try:
        init_db(conn)
        if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            for username, pwd, role in _DEFAULT_USERS:
                pwd_hash = bcrypt.hashpw(
                    pwd.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
                conn.execute(
                    "INSERT INTO users(id, username, pwd_hash, role, created_at) "
                    "VALUES (?, ?, ?, ?, datetime('now'))",
                    ("u_" + username, username, pwd_hash, role))
            conn.commit()
    finally:
        conn.close()