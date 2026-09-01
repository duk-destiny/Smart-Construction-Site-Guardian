"""P3 只读意图路由测试（v0.5）。

确定性覆盖：哈希 ID / 口语序数的抽取与定位、状态词列表分流、
逾期与统计动作、unknown 人工兜底、**全程零写入保证**（读写硬隔离回归）、
LLM 白名单兜底分支（monkeypatch，不发真实请求）。
"""
from __future__ import annotations

import pytest

from dao.db import get_conn, init_db
from dao.models import AuditDAO, RiskDAO, TaskDAO, UserDAO, WorkOrderDAO
from services.intent_router import IntentRouter
from services.permission_service import AuthorizationError

OLD, MID, NEW = ["2030-01-01 00:00:00", "2030-01-02 00:00:00",
                 "2030-01-03 00:00:00"]


@pytest.fixture
def env():
    conn = get_conn(":memory:")
    init_db(conn)
    users = UserDAO(conn)
    safety = users.insert("zhangsan", "hashed", "safety")
    lisi = users.insert("lisi", "hashed", "responsible")
    tasks = TaskDAO(conn)

    oids = []
    for i, ts in enumerate((OLD, MID, NEW)):
        tid = tasks.insert(safety, "{}", "completed")
        RiskDAO(conn).insert(tid, "一般", "[]", "[]")
        wid = WorkOrderDAO(conn).insert(
            task_id=tid, hazard_desc=f"隐患{i}", clause=None,
            requirement="限期整改", risk_level="一般", worker_notice="")
        # 压平时间以固定"最新序"语义：NEW 插入的是最新一张
        conn.execute("UPDATE work_orders SET created_at=? WHERE id=?",
                     (ts, wid))
        conn.execute("UPDATE tasks SET created_at=? WHERE id=?", (ts, tid))
        conn.commit()
        oids.append(wid)

    return {"conn": conn,
            "router": IntentRouter(conn, use_llm=False),
            "ids": {"safety": safety, "lisi": lisi},
            "order_ids": {"old": oids[0], "mid": oids[1], "new": oids[2]}}


def _audit_count(env):
    return env["conn"].execute(
        "SELECT COUNT(*) FROM audit_logs").fetchone()[0]


def test_extract_hash_and_numeric(env):
    x = IntentRouter.extract("#w_ab12cd34ef56 的进度 和 3号工单")
    assert x["hash_ids"] == ["w_ab12cd34ef56"]
    assert x["nums"] == [3]
    assert x["query_hint"] is True


def test_positional_number_resolves_to_third_newest(env):
    res = env["router"].route("3号工单怎么样了")
    assert res.action == "order_detail"
    assert res.order_id == env["order_ids"]["old"]      # 第 3 张 = 最旧


def test_hash_id_direct_detail(env):
    target = env["order_ids"]["new"]
    res = env["router"].route(f"帮我看下 {target} 状态如何")
    assert res.action == "order_detail" and res.order_id == target


def test_out_of_range_number_falls_to_human(env):
    res = env["router"].route("88号工单查一下")
    assert res.tier == "human"
    assert "超出" in (res.hint or "")


def test_status_keyword_routes_to_open_list(env):
    res = env["router"].route("整改中的还有几张？")
    assert res.action == "order_list"
    assert res.status == "open"
    assert len(res.candidates) == 3                     # 种子三张全为 open


def test_overdue_and_weekly_actions(env):
    r1 = env["router"].route("最近有没有逾期的？")
    r2 = env["router"].route("给我来一份周报")
    r3 = env["router"].route("近30天安全统计")
    assert (r1.action, r2.action, r3.action) == \
        ("overdue_stats", "weekly_stats", "weekly_stats")
    assert r3.days == 30


def test_unknown_text_never_executes(env):
    before_orders = env["conn"].execute(
        "SELECT COUNT(*) FROM work_orders").fetchone()[0]
    before_audit = _audit_count(env)
    res = env["router"].route("今天天气不错")
    assert res.tier == "human"
    assert env["conn"].execute(
        "SELECT COUNT(*) FROM work_orders").fetchone()[0] == before_orders
    assert _audit_count(env) == before_audit            # 只读铁证


def test_full_route_session_performs_zero_writes(env):
    texts = ["w_xxx不存在的话也别写库", "第2号的那张看看", "逾期了吗", "本周汇总"]
    b_o = env["conn"].execute("SELECT COUNT(*) FROM work_orders").fetchone()[0]
    b_a = _audit_count(env)
    for t in texts:
        env["router"].route(t)
    assert env["conn"].execute(
        "SELECT COUNT(*) FROM work_orders").fetchone()[0] == b_o
    assert _audit_count(env) == b_a


def test_llm_fallback_whitelist_branch(env, monkeypatch):
    """规则无把握且本地模型可用 → 走封闭集分类；越权字段被拒后回人工层。"""
    import core.llm_engine as lle
    lisi = env["ids"]["lisi"]

    # 预置一条可被语义命中的真实工单（哈希形式）
    tid = env["conn"].execute(
        "INSERT INTO tasks(id,user_id,permit_json,status,source,created_at) "
        "VALUES('t_llm',?, '{}','completed','upload','2030-01-05')",
        (lisi,))
    RiskDAO(env["conn"]).insert("t_llm", "一般", "[]", "[]")
    WorkOrderDAO(env["conn"]).insert(
        task_id="t_llm", hazard_desc="语义命中样本", clause=None,
        requirement="整改", risk_level="一般", worker_notice="")
    # 使其 ID 与 LLM 返回的封闭集结果一致（否则存在性校验会被拒回人工层）
    env["conn"].execute(
        "UPDATE work_orders SET id='w_llmsemtic01' WHERE task_id='t_llm'")
    env["conn"].commit()

    eng_calls = {"n": 0}

    class FakeClient:
        """v2.1 起第 2 层走统一 ChatClient——在接缝处注入，白名单语义不变。"""

        def available_provider(self):
            return "fake"

        def chat(self, system, user, *, json_schema=None, max_tokens=1024,
                 total_deadline_sec=30.0, provider=None) -> ChatResult:
            eng_calls["n"] += 1
            if "订机票" in user:
                return ChatResult(content={"intent": "NASTY_DROP_TABLE"},
                                  status="success")                # 越白名单
            return ChatResult(content={"intent": "order_status",
                                       "id": "w_llmsemtic01"},
                              status="success")

    from core.chat_client import ChatResult  # noqa: F401  供上方构造
    monkeypatch.setattr("core.chat_client.get_chat_client",
                        lambda: FakeClient())

    bad = IntentRouter(env["conn"], use_llm=True)
    # 让正则层无法命中 → 强制走 LLM 层；sample 中"样本"不带数字不触动静默
    r_bad = bad.route("帮我订机票顺便看看那条记录样本")
    assert eng_calls["n"] >= 1
    assert r_bad.tier in ("human", "rule")              # 越白名单不入执行端

    good = IntentRouter(env["conn"], use_llm=True)
    r_ok = good.route("上次说的那个语义命中样本后来呢")
    assert r_ok.tier == "llm"
    assert r_ok.action == "order_detail"


def test_permission_negative_still_unused_here_guard():
    """占位断言：确保本文件确实引用了权限异常类型（防误删导入）。"""
    assert AuthorizationError is not None


# ---------- 双层路由：认知路径前置识别（v2.1 §5.11，T8 实跑补） ----------

def test_cognitive_weekly_generation_routes_to_playbook(env):
    """「出一份本周安全周报」（§5.2 标准话术）→ weekly_report 认知路径。"""
    r = IntentRouter(env["conn"], use_llm=False).route("出一份本周安全周报")
    assert r.path == "cognitive" and r.action == "weekly_report"


def test_weekly_stats_fetch_stays_fast(env):
    """纯取数话术不进认知层：走 weekly_stats 规则快路径（含导出周报）。"""
    for text in ("本周统计", "给我来一份周报", "导出周报", "近7天统计"):
        r = IntentRouter(env["conn"], use_llm=False).route(text)
        assert r.path == "fast", text
        assert r.action == "weekly_stats", text


def test_cognitive_video_analysis_routes(env):
    r = IntentRouter(env["conn"], use_llm=False).route(
        "帮我看看这段视频有没有违规")
    assert r.path == "cognitive" and r.action == "video_analysis"


def test_cognitive_root_cause_routes(env):
    for text in ("为什么会逾期", "帮我根因分析一下近期告警"):
        r = IntentRouter(env["conn"], use_llm=False).route(text)
        assert r.path == "cognitive", text
        assert r.action == "root_cause_analysis", text


def test_knowledge_base_query_routes_cognitive(env):
    """「帮我查询知识库」（v2.2 对话窗口实跑误路由修复）→ evidence_query 认知层。"""
    for text in ("帮我查询知识库", "看一下知识库规范", "查一下规范要求"):
        r = IntentRouter(env["conn"], use_llm=False).route(text)
        assert r.path == "cognitive", text
        assert r.action == "evidence_query", text
