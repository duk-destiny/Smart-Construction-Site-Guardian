"""Task 2：数据库 Schema 与初始化测试。"""
import sqlite3

import pytest

from dao.db import get_conn, init_db
from dao.models import AgentChatDAO, AuditDAO, UserDAO


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


# ============================================================
# 认知层四表（设计文档 §5.5/§5.6）：建表 / AgentChatDAO / 清空白名单
# ============================================================

def test_init_creates_agent_chat_tables_and_indexes():
    """init_db 后认知层四表与索引均存在；agent_runs 不受影响。"""
    conn = _fresh()
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ("chat_sessions", "chat_messages",
              "agent_chat_runs", "agent_chat_run_steps"):
        assert t in tables, f"缺少认知层表 {t}"
    assert "agent_runs" in tables  # 老表不动仍在
    indexes = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    for i in ("idx_chatmsg_session", "idx_agentchat_status",
              "idx_agentchat_steps", "idx_agentchat_session"):
        assert i in indexes, f"缺少索引 {i}"
    # 七态 CHECK 生效：非法状态拒收，合法七态可写（回滚不留数据）
    conn.execute("BEGIN")
    conn.execute(
        "INSERT INTO users(id,username,pwd_hash,role,created_at) "
        "VALUES('u1','x','h','safety',datetime('now'))")
    conn.execute(
        "INSERT INTO chat_sessions(id,user_id,created_at) "
        "VALUES('s1','u1',datetime('now'))")
    try:
        conn.execute(
            "INSERT INTO agent_chat_runs"
            "(id,session_id,user_id,user_input,status,created_at,updated_at) "
            "VALUES('r1','s1','u1','q','not_a_status',datetime('now'),datetime('now'))")
        assert False, "非法状态应被 CHECK 拒绝"
    except sqlite3.IntegrityError:
        pass
    conn.rollback()


def _seed_agent_chat(conn) -> tuple[str, AgentChatDAO]:
    uid = UserDAO(conn).insert("alice", "hash", "safety")
    return uid, AgentChatDAO(conn)


def test_agent_chat_dao_full_flow():
    """建会话 → 写消息 → 建 run → 写 step → 条件状态翻转全链路。"""
    conn = _fresh()
    uid, dao = _seed_agent_chat(conn)

    # 会话与消息
    sid = dao.create_session(uid, title="周报会话")
    assert dao.get_session(sid)["title"] == "周报会话"
    mid = dao.insert_message(sid, "user", "出一份本周安全周报", intent="weekly_report")
    assert isinstance(mid, int)
    msgs = dao.list_messages(sid)
    assert len(msgs) == 1 and msgs[0]["role"] == "user"

    # run 创建默认 pending、deadline_sec 默认 30.0、current_step_idx=-1
    rid = dao.create_run(sid, uid, "出一份本周安全周报", intent="weekly_report")
    run = dao.get_run(rid)
    assert run["status"] == "pending"
    assert run["deadline_sec"] == 30.0
    assert run["current_step_idx"] == -1
    assert run["task_id"] is None  # 可空、无外键桥接字段
    assert dao.get_run_by_session(sid)["id"] == rid

    # 非状态字段白名单更新（含 need_confirm 强制置位与 task_id 回填）
    dao.update_run(rid, plan_json='{"steps":[]}', current_step_idx=0,
                   need_confirm=True, task_id="t_bridged")
    run = dao.get_run(rid)
    assert run["plan_json"] == '{"steps":[]}'
    assert run["need_confirm"] == 1 and run["task_id"] == "t_bridged"

    # 步骤写入与回填；list_steps 按 step_idx 升序；步级状态四态 CHECK
    dao.insert_step(rid, 0, "weekly_report_data", args_json='{}')
    dao.insert_step(rid, 1, "rag_search", args_json='{"q":"高处作业"}')
    dao.update_step(rid, 0, "success", result_digest="统计 5 项", cost_ms=12)
    dao.update_step(rid, 1, "degraded", error="rag 超时")
    steps = dao.list_steps(rid)
    assert [s["step_idx"] for s in steps] == [0, 1]
    assert steps[0]["status"] == "success" and steps[0]["cost_ms"] == 12
    assert steps[1]["status"] == "degraded" and steps[1]["error"] == "rag 超时"
    try:
        dao.insert_step(rid, 2, "drop_tables")
        dao.conn.execute(
            "UPDATE agent_chat_run_steps SET status='boom' "
            "WHERE run_id=? AND step_idx=2", (rid,))
        assert False, "非法步骤状态应被 CHECK 拒绝"
    except sqlite3.IntegrityError:
        conn.rollback()

    # 条件状态翻转：成功路径 pending → running → pending_confirm → running → completed
    assert dao.transition_status(rid, "pending", "running") is True
    assert dao.transition_status(rid, "running", "pending_confirm") is True
    assert dao.transition_status(rid, "pending_confirm", "running") is True
    assert dao.transition_status(rid, "running", "completed",
                                 result_json='{"answer":"周报已生成"}') is True
    run = dao.get_run(rid)
    assert run["status"] == "completed" and run["error"] is None

    # 竞争失败：预期状态与实际不符时返回 False 且不改变状态（防重复处置）
    assert dao.transition_status(rid, "running", "completed") is False
    assert dao.get_run(rid)["status"] == "completed"
    assert dao.transition_status(rid, "failed", "cancelled") is False
    assert dao.get_run(rid)["status"] == "completed"

    # 孤儿扫描查询：按状态集 + updated_at 阈值
    rid2 = dao.create_run(sid, uid, "根因分析")
    orphans = dao.list_runs_by_status(("pending", "running"))
    assert any(r["id"] == rid2 for r in orphans)
    assert not any(r["id"] == rid for r in orphans)  # completed 不在扫描集
    # 会话列表含新会话；消息可挂 run_id 与 digest 回写（§5.7 只存摘要）
    dao.insert_message(sid, "assistant", "周报已生成", run_id=rid,
                       digest="本周告警 3 起，均已闭环")
    assert dao.list_messages(sid)[-1]["run_id"] == rid
    assert dao.list_sessions(uid)[0]["id"] == sid


def test_agent_chat_step_unique_conflict():
    """UNIQUE(run_id, step_idx)：同一步重复落库抛 IntegrityError（幂等恢复依据）。"""
    conn = _fresh()
    uid, dao = _seed_agent_chat(conn)
    sid = dao.create_session(uid)
    rid = dao.create_run(sid, uid, "视频分析")
    dao.insert_step(rid, 0, "run_video_pipeline")
    with pytest.raises(sqlite3.IntegrityError):
        dao.insert_step(rid, 0, "run_video_pipeline")
    # 冲突后既有行未被覆盖，同 run 其他步号仍可写入（事务已隔离）
    assert dao.get_step(rid, 0)["tool"] == "run_video_pipeline"
    dao.insert_step(rid, 1, "rag_search")
    assert len(dao.list_steps(rid)) == 2


def test_clear_all_data_covers_agent_chat_in_fk_order():
    """TaskService.clear_all_data 按外键方向清空认知层四表不报错。"""
    from services.task_service import TaskService

    # 白名单顺序断言：子表先删（chat_messages → steps → runs → chat_sessions）
    order = TaskService._CLEARABLE_TABLES
    assert order.index("chat_messages") < order.index("agent_chat_run_steps")
    assert order.index("agent_chat_run_steps") < order.index("agent_chat_runs")
    assert order.index("agent_chat_runs") < order.index("chat_sessions")

    # 实走清空路径：外键开启下删带认知数据的库不报 FK 错误，四表清零，审计留痕
    conn = _fresh()
    uid, dao = _seed_agent_chat(conn)
    sid = dao.create_session(uid)
    rid = dao.create_run(sid, uid, "周报")
    dao.insert_message(sid, "user", "周报", run_id=rid)
    dao.insert_step(rid, 0, "weekly_report_data")
    svc = TaskService(conn)
    conn.execute(
        "UPDATE users SET role='admin' WHERE id=?", (uid,))
    conn.commit()
    result = svc.clear_all_data(uid, "RESET")
    assert result["ok"] is True
    for t in ("chat_messages", "agent_chat_run_steps",
              "agent_chat_runs", "chat_sessions"):
        cnt = conn.execute(f"SELECT COUNT(*) AS c FROM {t}").fetchone()["c"]
        assert cnt == 0, f"{t} 未清空"
    audit = conn.execute(
        "SELECT COUNT(*) AS c FROM audit_logs WHERE action='clear_data'"
    ).fetchone()["c"]
    assert audit == 1


# ---------- v2.2 对话窗口：删会话前兜底取消未完结 run ----------

def test_cancel_active_runs_then_delete_session():
    """删除兜底：未完结 run 置 cancelled（轮询得终态而非 404），终态 run 不动。"""
    from dao.models import AgentChatDAO, UserDAO

    conn = get_conn(":memory:")
    init_db(conn)
    uid = UserDAO(conn).insert("u1", "h", "safety")
    dao = AgentChatDAO(conn)
    sid = dao.create_session(uid)
    rid_active = dao.create_run(sid, uid, "进行中")
    dao.transition_status(rid_active, "pending", "running")
    rid_done = dao.create_run(sid, uid, "已完成")
    dao.transition_status(rid_done, "pending", "running")
    dao.transition_status(rid_done, "running", "completed")

    assert dao.cancel_active_runs(sid) == 1          # 只取消未完结
    assert dao.get_run(rid_active)["status"] == "cancelled"
    assert "会话删除" in (dao.get_run(rid_active)["error"] or "")
    assert dao.get_run(rid_done)["status"] == "completed"   # 终态不覆写

    assert dao.delete_session(sid) == 1
    assert dao.get_run(rid_active) is None
    assert dao.get_run(rid_done) is None
