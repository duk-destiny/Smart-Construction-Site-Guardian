"""审计服务测试（TDD：仅追加、返回 log_id）。"""

from dao.db import get_conn, init_db
from services.audit_service import AuditService


def test_audit_append_returns_id():
    conn = get_conn(":memory:")
    init_db(conn)
    svc = AuditService(conn)
    r = svc.append("u1", "login", {"ip": "x"})
    assert r["ok"] is True
    assert isinstance(r["data"]["log_id"], int)


def test_audit_append_only_no_delete():
    """审计表无 delete 路径：AuditDAO 不提供 delete 方法（C4）。"""
    from dao.models import AuditDAO
    assert not hasattr(AuditDAO, "delete")
    assert not hasattr(AuditDAO, "update")


def test_audit_records_increasing():
    conn = get_conn(":memory:")
    init_db(conn)
    svc = AuditService(conn)
    svc.append("u1", "a", {})
    svc.append("u2", "b", {})
    rows = conn.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0]
    assert rows == 2
    conn.close()
