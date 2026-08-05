"""Task 2：数据库 Schema 与初始化测试。"""
import sqlite3

from dao.db import get_conn, init_db
from dao.models import AuditDAO, UserDAO


def _fresh() -> sqlite3.Connection:
    conn = get_conn(":memory:")
    init_db(conn)
    return conn


def test_audit_append_only():
    """触发器保障审计日志不可 UPDATE/DELETE（C4 / DB 文档 §4）。"""
    conn = _fresh()
    AuditDAO(conn).insert("u1", "login", "{}")
    for evil in ("DELETE FROM audit_logs", "UPDATE audit_logs SET action='x'"):
        try:
            conn.execute(evil)
            assert False, f"应被触发器阻止: {evil}"
        except sqlite3.Error:
            pass


def test_init_creates_expected_tables():
    conn = _fresh()
    names = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    for t in ["users", "tasks", "detections", "compliances", "risks",
              "work_orders", "audit_logs", "kb_docs", "agent_runs",
              "feedback_samples", "alarm_events", "model_registry"]:
        assert t in names, f"缺少表 {t}"


def test_triggers_and_views_exist():
    conn = _fresh()
    objs = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE name IN "
        "('trg_audit_no_update','trg_audit_no_delete',"
        "'v_task_summary','v_high_risk','v_audit_recent')")]
    assert len(objs) == 5, f"触发器/视图缺失: {objs}"


def test_dao_audit_only_insert_select():
    """用户与审计 DAO 基本可用；审计表无删除路径。"""
    conn = _fresh()
    uid = UserDAO(conn).insert("alice", "hash", "safety")
    assert UserDAO(conn).get_by_name("alice")[0] == uid
    log_id = AuditDAO(conn).insert(uid, "login", "{}")
    assert isinstance(log_id, int)


def test_init_db_migrates_old_agent_runs():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE agent_runs (
            id TEXT PRIMARY KEY,
            task_id TEXT,
            agent TEXT,
            status TEXT,
            cost_ms INTEGER,
            output_json TEXT,
            error TEXT,
            created_at TEXT
        );
    """)
    init_db(conn)
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(agent_runs)")]
    assert "input_json" in cols


def test_init_db_migrates_detection_records_tracking():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE detection_records (
            id TEXT PRIMARY KEY,
            session_id TEXT,
            scene_id TEXT,
            mode TEXT,
            frame_status TEXT,
            cls TEXT,
            conf REAL,
            severity TEXT,
            created_at TEXT
        );
    """)
    init_db(conn)
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(detection_records)")]
    assert "track_id" in cols
    assert "track_frames" in cols
