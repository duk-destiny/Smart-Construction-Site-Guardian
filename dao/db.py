"""数据库连接与初始化：WAL 模式 + 外键开启 + 执行 schema。

统一经本模块获取连接，保证 journal_mode=WAL、foreign_keys=ON（DB 文档 §1/§7）。
init_db 按库文件路径记忆化（进程内只对同一物理库执行一次全量 schema 脚本），
实时链路每帧调用 get_conn+init_db 时不重复解析执行整份 schema.sql。
"""
from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path

DEFAULT_DB_PATH = str(Path(__file__).resolve().parent.parent / "data" / "app.db")

_MIGRATIONS: dict[str, list[tuple[str, str]]] = {
    "detection_records": [
        ("track_id", "TEXT"),
        ("track_frames", "INTEGER"),
    ],
    "agent_runs": [("input_json", "TEXT")],
    "alarm_events": [
        ("image_path", "TEXT"),
        ("source", "TEXT"),
        ("clause", "TEXT"),
    ],
    "feedback_samples": [
        ("image_path", "TEXT"),
        ("detection_json", "TEXT"),
        ("corrected_labels_json", "TEXT"),
        ("status", "TEXT NOT NULL DEFAULT 'pending'"),
        ("reviewed_by", "TEXT"),
        ("reviewed_at", "TEXT"),
    ],
    # v0.2 工单闭环：派发/整改/验收生命周期字段
    "work_orders": [
        ("assignee_id", "TEXT REFERENCES users(id)"),
        ("status", "TEXT NOT NULL DEFAULT 'open'"),
        ("dispatched_at", "TEXT"),
        ("deadline", "TEXT"),
        ("submitted_note", "TEXT"),
        ("submitted_imgs", "TEXT"),
        ("approved_by", "TEXT"),
        ("approved_at", "TEXT"),
        ("closed_at", "TEXT"),
        ("review_reason", "TEXT"),
    ],
    # v0.2 任务来源标记（camera/upload/text），供台账区分感知与人工上报
    "tasks": [
        ("source", "TEXT NOT NULL DEFAULT 'upload'"),
    ],
}

def get_conn(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
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


_SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")
# 已执行过 schema 的物理库 → 执行时的 schema.sql mtime；mtime 变化则重跑
_SCHEMA_DONE: dict[str, float] = {}
_SCHEMA_LOCK = threading.Lock()


def _db_file_key(conn: sqlite3.Connection) -> str | None:
    """取 main 库的文件路径作为记忆化键；无路径（:memory: 等）返回 None。"""
    try:
        for _seq, _name, file_path in conn.execute("PRAGMA database_list"):
            if file_path:
                return os.path.normpath(os.path.abspath(file_path))
    except Exception:  # noqa: BLE001 无法取路径时退回"每次全量执行"
        return None
    return None


def _migrate_user_role_check(conn: sqlite3.Connection) -> bool:
    """老库 users.role 的 CHECK 约束缺少 'responsible' 角色时做一次受控表重建。

    SQLite 无法 ALTER 修改 CHECK 约束；标准十二步简化为本库场景：
    users 行数极小、任务侧 FK 按表名引用，临时关闭外键完成
    建新表→拷贝→换名 即可，数据无损。返回是否执行了重建。
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='users'"
    ).fetchone()
    if row is None or not row["sql"]:
        return False
    if "responsible" in (row["sql"] or ""):
        return False  # 新建库由 schema.sql 直接带全量角色，无需迁移

    # 关闭外键必须在事务外才生效（python sqlite3 隐式事务防护）
    conn.commit()
    conn.execute("PRAGMA foreign_keys=OFF")
    # 暂存并摘除引用 users 的视图/触发器（DROP 后 RENAME 才能通过依赖校验）
    deps = conn.execute(
        "SELECT type, name, sql FROM sqlite_master "
        "WHERE type IN ('view','trigger') AND sql LIKE '%users%' "
        "AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    for dep in deps:
        conn.execute(f"DROP {dep['type'].upper()} IF EXISTS \"{dep['name']}\"")
    conn.execute("""
        CREATE TABLE users_new (
            id         TEXT PRIMARY KEY,
            username   TEXT NOT NULL UNIQUE,
            pwd_hash   TEXT NOT NULL,
            role       TEXT NOT NULL CHECK(role IN ('safety','admin','responsible')),
            created_at TEXT NOT NULL
        )
    """)
    conn.execute(
        "INSERT INTO users_new(id,username,pwd_hash,role,created_at) "
        "SELECT id,username,pwd_hash,role,created_at FROM users"
    )
    conn.execute("DROP TABLE users")
    conn.execute("ALTER TABLE users_new RENAME TO users")
    for dep in deps:
        if dep["sql"]:
            conn.execute(dep["sql"])
    conn.commit()
    conn.execute("PRAGMA foreign_keys=ON")
    return True


def init_db(conn: sqlite3.Connection) -> None:
    """执行 schema.sql（建表/索引/触发器/视图）+ 增量迁移列。

    同一进程内对同一物理库只全量执行一次（以库文件路径 + schema.sql mtime
    记忆化）；重复调用为 O(1) 返回，供实时帧持久化等高频路径安全复用。
    注意：若外部在进程运行期间删表，需重启进程（或换新连接文件名）才会重建。
    """
    key = _db_file_key(conn)
    try:
        schema_mtime = os.path.getmtime(_SCHEMA_PATH)
    except OSError:
        schema_mtime = 0.0
    with _SCHEMA_LOCK:
        if key is not None and _SCHEMA_DONE.get(key) == schema_mtime:
            return
        with open(_SCHEMA_PATH, encoding="utf-8") as f:
            conn.executescript(f.read())
        _migrate_user_role_check(conn)
        for table, columns in _MIGRATIONS.items():
            existing = {
                row["name"]
                for row in conn.execute(f"PRAGMA table_info({table})")
            }
            for column, ddl in columns:
                if column not in existing:
                    conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        conn.commit()
        if key is not None:
            _SCHEMA_DONE[key] = schema_mtime
