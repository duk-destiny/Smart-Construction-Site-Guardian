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


def test_init_creates_eight_tables():
    conn = _fresh()
    names = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    for t in ["users", "tasks", "detections", "compliances", "risks",
              "work_orders", "audit_logs", "kb_docs"]:
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
