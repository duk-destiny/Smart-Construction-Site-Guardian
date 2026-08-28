"""风险周报服务测试（v0.3）。

直接以固定日期种子三类事实源（检测帧/告警/工单），验证聚合口径、
按责任人逾期率与规则化结论；PDF 渲染校验产物头部与体量。
不依赖真实时钟，全部日期字面量驱动。
"""
from __future__ import annotations

import os

import pytest

from dao.db import get_conn, init_db
from services.report_service import WeeklyReportService

S, E = "2030-01-04", "2030-01-10"          # 统计周期
IN_PERIOD = ["2030-01-04 08:00:00", "2030-01-07 12:00:00",
             "2030-01-10 23:00:00"]
AS_OF_END = f"{E} 23:59:59"


@pytest.fixture
def env():
    conn = get_conn(":memory:")
    init_db(conn)
    users = {"lisi": "u_lisi", "wangwu": "u_wangwu", "admin": "u_admin_l"}
    ins_user(conn, users["lisi"], "responsible")
    ins_user(conn, users["wangwu"], "responsible")
    ins_user(conn, users["admin"], "admin")

    # 检测帧：周期内 10 帧（spark×4 / smoke×2 / none 占位×4），另 3 帧在周期外
    frames = [
        ("2030-01-04 09:00:00", "spark", "不合规"),
        ("2030-01-05 10:00:00", "spark", "不合规"),
        ("2030-01-06 11:00:00", "smoke", "不合规"),
        ("2030-01-07 12:00:00", "spark", "不合规"),
        ("2030-01-08 13:00:00", "smoke", "不合规"),
        ("2030-01-08 14:00:00", "none", "合规"),
        ("2030-01-09 15:00:00", "none", "警告"),
        ("2030-01-09 16:00:00", "none", "合规"),
        ("2030-01-10 17:00:00", "helmet", "安全"),   # cls 白名单外的杂项
        ("2029-12-31 00:00:00", "spark", "不合规"),  # 周期外
    ]
    for i, (ts, cls, status) in enumerate(frames):
        conn.execute(
            "INSERT INTO detection_records(id,session_id,scene_id,mode,"
            "frame_status,cls,conf,severity,track_id,track_frames,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (f"r{i}", f"s{i % 2}", None, "realtime", status, cls,
             0.9 if cls != "none" else 1.0,
             "critical" if cls in ("spark", "smoke") else "safe",
             None, 1, ts))

    # 告警：周期内 new×2 / resolved×1，周期外 new×1
    alarms = [
        ("al1", "new", "2030-01-05 09:00:00"),
        ("al2", "new", "2030-01-08 09:00:00"),
        ("al3", "resolved", "2030-01-09 09:00:00"),
        ("al4", "new", "2030-01-02 09:00:00"),
    ]
    for aid, st_, ts in alarms:
        conn.execute(
            "INSERT INTO alarm_events(id,session_id,task_id,scene_id,cls,conf,"
            "image_path,source,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (aid, None, None, "hot_work", "spark", 0.9, None, "rtsp_bg", st_, ts))

    # 工单：周期内 5 张 + 责任人画像（任务行先行满足外键）
    wo_seed = [
        # (id, created, status, deadline, assignee)
        ("w1", "2030-01-04 10:00:00", "closed",    "2030-01-06 00:00:00", users["lisi"]),
        ("w2", "2030-01-05 10:00:00", "closed",    "2030-01-07 00:00:00", users["lisi"]),
        ("w3", "2030-01-06 10:00:00", "submitted", "2030-01-12 00:00:00", users["lisi"]),
        ("w4", "2030-01-07 10:00:00", "open",      "2030-01-20 00:00:00", users["wangwu"]),  # 未逾期
        ("w5", "2030-01-07 11:00:00", "open",      "2030-01-08 00:00:00", users["wangwu"]),  # 已逾期
        ("w6", "2029-12-20 10:00:00", "open",      "2030-01-30 00:00:00", users["lisi"]),   # 周期外，不计新增
    ]
    for wid, created, status, deadline, assignee in wo_seed:
        conn.execute(
            "INSERT INTO tasks(id,user_id,permit_json,status,source,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (f"t_{wid}", assignee, "{}", "completed", "upload", created))
        conn.execute(
            "INSERT INTO work_orders(id,task_id,hazard_desc,risk_level,"
            "worker_notice,assignee_id,status,deadline,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (wid, f"t_{wid}", "隐患", "一般", "", assignee, status, deadline,
             created))
    conn.commit()
    return {"conn": conn, "svc": WeeklyReportService(conn), "users": users}


def ins_user(conn, uid: str, role: str) -> None:
    import bcrypt
    h = bcrypt.hashpw(b"x", bcrypt.gensalt()).decode()
    conn.execute(
        "INSERT INTO users(id,username,pwd_hash,role,created_at) "
        "VALUES(?,?,?,?,datetime('now'))", (uid, uid.replace("u_", ""), h, role))
    conn.commit()


def test_gather_detection_summary_filters_period(env):
    s = env["svc"].gather(S, E)
    assert s["frames"] == 9           # 剔除周期外 1 帧
    assert s["bad"] == 5 and s["ok"] == 2 and s["warn"] == 1
    assert s["top_classes"][0] == {"cls": "spark", "count": 3}


def test_gather_alarm_counts(env):
    s = env["svc"].gather(S, E)
    assert s["alarms_by_status"] == {"new": 2, "resolved": 1}


def test_gather_order_funnel_and_overdue(env):
    s = env["svc"].gather(S, E)
    assert s["orders_total"] == 5                          # w6 周期外不计新增
    assert s["orders_by_status"]["closed"] == 2
    assert s["orders_by_status"]["submitted"] == 1
    assert s["overdue_open_now"] == 1                      # 仅 w5 存量逾期


def test_per_assignee_overdue_rate(env):
    s = env["svc"].gather(S, E)
    by_name = {a["name"]: a for a in s["per_assignee"]}
    lisi, wangwu = by_name[env["users"]["lisi"].replace("u_", "")], \
        by_name[env["users"]["wangwu"].replace("u_", "")]
    assert lisi["assigned"] == 3 and lisi["closed_n"] == 2 and lisi["overdue_n"] == 0
    assert wangwu["assigned"] == 2 and wangwu["overdue_n"] == 1
    assert wangwu["overdue_rate"] == pytest.approx(0.5)


def test_conclusions_rule_based(env):
    text = "\n".join(env["svc"].gather(S, E)["conclusions"])
    assert "销项率" in text          # 有工单必有销项率行
    assert "1 张逾期未整改" in text  # 存在逾期必触发督办结论
    assert "最高频隐患类别：spark" in text


def test_render_pdf_output(env, tmp_path):
    svc = env["svc"]
    stats = svc.gather(S, E)
    out = str(tmp_path / "weekly.pdf")
    svc.render_pdf(stats, out)
    blob = open(out, "rb").read()
    assert blob.startswith(b"%PDF") and len(blob) > 2000


def test_generate_audits_and_writes_file(env, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)     # 隔离 data/exports 写入
    res = env["svc"].generate(S, E, user_id="u_admin_l", out_dir=str(tmp_path / "ex"))
    assert res["ok"] is True
    assert os.path.exists(res["data"]["file_path"])
    actions = [r["action"] for r in env["conn"].execute(
        "SELECT action FROM audit_logs").fetchall()]
    assert "report_generate" in actions


def test_generate_requires_export_permission(env, tmp_path):
    # responsible 无 export 权限 → AuthorizationError（自定义异常类）
    from services.permission_service import AuthorizationError as PErr
    with pytest.raises(PErr):
        env["svc"].generate(S, E, user_id="u_lisi", out_dir=str(tmp_path))
