"""导出服务测试（TDD：构建内存 DB → 导出 → 校验文件）。"""

import os

from dao.db import get_conn, init_db
from dao.models import UserDAO, WorkOrderDAO
from services.export_service import ExportService


def _seed(conn):
    uid = UserDAO(conn).insert("seed_admin", "hash", "admin")
    conn.execute(
        "INSERT INTO tasks(id,user_id,permit_json,status,created_at) "
        "VALUES(?,?,?,?,datetime('now'))", ("t_1", uid, "{}", "done"))
    conn.commit()
    WorkOrderDAO(conn).insert(
        task_id="t_1", hazard_desc="动火现场发现火花且无监火人",
        clause="第一条", requirement="立即停工整改",
        risk_level="重大", worker_notice="兄弟，动火必须有专人看着...")
    return "t_1"


def test_export_creates_file():
    """导出生成 .xlsx 且含数据行。"""
    conn = get_conn(":memory:")
    init_db(conn)
    _seed(conn)

    svc = ExportService(conn=conn)
    r = svc.export_excel(task_id="t_1")
    assert r["ok"]
    fpath = r["data"]["file_path"]
    assert fpath.endswith(".xlsx")
    assert os.path.exists(fpath)
    assert r["data"]["rows"] == 1
    conn.close()


def test_export_no_data(tmp_path):
    """无工单时导出空表不报错。"""
    conn = get_conn(":memory:")
    init_db(conn)
    svc = ExportService(conn=conn)
    r = svc.export_excel(task_id="nonexistent")
    assert r["ok"]
    assert r["data"]["rows"] == 0
    conn.close()
