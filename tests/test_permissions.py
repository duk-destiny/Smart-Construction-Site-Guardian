"""服务层权限校验测试。"""

import pytest

from dao.db import get_conn, init_db
from dao.models import UserDAO
from services.kb_admin import KbAdmin
from services.permission_service import AuthorizationError
from services.task_service import TaskService


def test_service_permission_required():
    conn = get_conn(":memory:")
    init_db(conn)
    uid = UserDAO(conn).insert("safety", "hash", "safety")
    svc = TaskService(conn)
    tid = svc.create_task(uid, [], {"watcher": "张三"})

    with pytest.raises(AuthorizationError):
        svc.manual_override(tid, "一般", "无用户", user_id=None)

    with pytest.raises(AuthorizationError):
        KbAdmin(conn).import_pdf("missing.pdf", uid)


def test_clear_data_admin_only_and_reset_confirmation():
    conn = get_conn(":memory:")
    init_db(conn)
    safety_uid = UserDAO(conn).insert("safety", "hash", "safety")
    admin_uid = UserDAO(conn).insert("admin", "hash", "admin")
    svc = TaskService(conn)
    svc.create_task(safety_uid, [], {"watcher": "张三"})

    with pytest.raises(AuthorizationError):
        svc.clear_all_data(safety_uid, "RESET")

    with pytest.raises(ValueError):
        svc.clear_all_data(admin_uid, "reset")

    assert conn.execute("SELECT COUNT(*) AS cnt FROM tasks").fetchone()["cnt"] == 1

    result = svc.clear_all_data(admin_uid, "RESET")
    assert result["ok"] is True
    assert result["deleted"]["tasks"] == 1
    assert conn.execute("SELECT COUNT(*) AS cnt FROM tasks").fetchone()["cnt"] == 0
    assert conn.execute("SELECT COUNT(*) AS cnt FROM audit_logs").fetchone()["cnt"] == 1
