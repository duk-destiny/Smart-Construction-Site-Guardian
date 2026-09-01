"""/agent/* 六端点测试（设计文档 §5.12/§5.13）：全端点鉴权 401、
跨属主 404、confirm 202。仿 tests/test_api.py 的 httpx ASGITransport
范式；Fake ChatClient + Fake 工具注册表注入，不打网络。
"""
from __future__ import annotations

import asyncio
import time

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from core.chat_client import ChatResult

# 种子账号（与 tests/test_api.py 同源：core/bootstrap._DEFAULT_USERS）
SAFETY = ("safety", "demo1234")
RESP = ("lisi", "demo1234")

_TERMINAL = ("completed", "degraded", "failed", "cancelled")


class NoArgs(BaseModel):
    pass


class FakeChatClient:
    """规划返脚本计划；汇总返固定文案。"""

    def __init__(self, plan_obj: dict):
        self._plan = plan_obj

    def chat(self, system, user, *, json_schema=None, max_tokens=1024,
             total_deadline_sec=30.0, provider=None) -> ChatResult:
        if json_schema is not None:
            return ChatResult(content=self._plan, status="success")
        return ChatResult(content="任务已完成", status="success")


def _mk_registry(pay_calls: dict):
    from services.agent.tools import ToolSpec

    def _echo(args, ctx):
        return {"status": "success", "data": {"echo": True}}

    def _pay(args, ctx):
        pay_calls["n"] += 1
        return {"status": "success", "data": {"queued": True}}

    return {
        "echo": ToolSpec(fn=_echo, desc="回显", args_schema=NoArgs),
        "pay": ToolSpec(fn=_pay, desc="副作用", args_schema=NoArgs,
                        side_effect=True),
    }


@pytest.fixture()
def app_env(tmp_path, monkeypatch):
    """临时库 + 种子账号 + Fake 注入的 app（ASGITransport 不触发 lifespan）。"""
    import dao.db as dao_db
    from services import auth_service
    from services.agent.run_service import PlanRunService

    db_file = str(tmp_path / "agent_api.db")
    monkeypatch.setattr(dao_db, "DEFAULT_DB_PATH", db_file)
    auth_service._FAILS.clear()
    monkeypatch.setenv("API_PREWARM", "0")

    pay_calls = {"n": 0}
    monkeypatch.setattr(PlanRunService, "_REGISTRY", _mk_registry(pay_calls))
    monkeypatch.setattr(PlanRunService, "_CHAT_FACTORY",
                        lambda: FakeChatClient(
                            {"goal": "测试",
                             "steps": [{"tool": "echo", "args": {},
                                        "reason": "查"}]}))
    with PlanRunService._RUN_LOCK:
        PlanRunService._active_runs.clear()

    from core.bootstrap import ensure_initialized

    ensure_initialized()
    from api.main import create_app

    app = create_app()
    yield {"app": app, "db_file": db_file, "pay_calls": pay_calls,
           "monkeypatch": monkeypatch}
    with PlanRunService._RUN_LOCK:
        PlanRunService._active_runs.clear()


@pytest_asyncio.fixture
async def client(app_env):
    transport = ASGITransport(app=app_env["app"])
    async with AsyncClient(transport=transport,
                           base_url="http://testserver") as c:
        yield c


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _staff_token(client: AsyncClient) -> str:
    return await _login(client, SAFETY)


# 最小合法 PNG 魔数 + 填充（upload_guard 魔数校验通过；不用十六进制转义，
# 纯 bytes() 构造保证源码 ASCII 安全）
_PNG_MAGIC = bytes([0x89]) + b"PNG" + bytes([0x0D, 0x0A, 0x1A, 0x0A])


def _png() -> bytes:
    return _PNG_MAGIC + b"0" * 64


async def _login(client: AsyncClient, who: tuple[str, str]) -> str:
    r = await client.post("/api/auth/login",
                          json={"username": who[0], "password": who[1]})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _poll_status(db_file: str, run_id: str, statuses: tuple[str, ...],
                 timeout: float = 10.0) -> str:
    """直连库轮询直至落入目标状态集。"""
    from dao.db import get_conn
    from dao.models import AgentChatDAO

    conn = get_conn(db_file)
    try:
        dao = AgentChatDAO(conn)
        t0 = time.time()
        last = ""
        while time.time() - t0 < timeout:
            row = dao.get_run(run_id)
            last = row["status"]
            if last in statuses:
                return last
            time.sleep(0.05)
        return last
    finally:
        conn.close()


async def _wait_async(run_id: str, db_file: str,
                      statuses: tuple[str, ...],
                      timeout: float = 10.0) -> str:
    """异步轮询（让出事件循环，避免阻塞 worker 提交后的端点调用）。"""
    t0 = time.time()
    last = ""
    while time.time() - t0 < timeout:
        last = await asyncio.to_thread(_poll_status, db_file, run_id,
                                       statuses, 0.2)
        if last in statuses:
            return last
    return last


# ---------- 鉴权：六端点无/伪 token 一律 401 ----------

@pytest.mark.asyncio
async def test_all_endpoints_require_auth(client):
    """未认证访问六个端点全部 401（§5.13）。"""
    checks = [
        ("post", "/api/agent/chat", {"text": "查一下"}),
        ("get", "/api/agent/runs/acr_x/progress", None),
        ("get", "/api/agent/runs/acr_x/trace", None),
        ("post", "/api/agent/runs/acr_x/confirm", {"action": "confirm"}),
        ("post", "/api/agent/runs/acr_x/cancel", None),
        ("get", "/api/agent/sessions/acs_x/history", None),
    ]
    for method, url, body in checks:
        if method == "get":
            r = await client.get(url)
        else:
            r = await client.post(url, json=body)
        assert r.status_code == 401, f"{url} → {r.status_code}: {r.text}"
    # 伪造 token 同样 401
    r = await client.post("/api/agent/chat", json={"text": "查一下"},
                          headers=_auth("forged.token.here"))
    assert r.status_code == 401


# ---------- chat → progress/trace/history 正路径 ----------

@pytest.mark.asyncio
async def test_chat_creates_cognitive_run(client, app_env):
    """POST /agent/chat 建认知 run：返 cognitive 路径 + pending。"""
    token = await _login(client, SAFETY)
    r = await client.post("/api/agent/chat", json={"text": "帮我查一下"},
                          headers=_auth(token))
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["path"] == "cognitive"
    assert data["status"] == "pending"
    run_id, session_id = data["run_id"], data["session_id"]

    final = await _wait_async(run_id, app_env["db_file"], _TERMINAL)
    assert final == "completed"

    # progress / trace
    r = await client.get(f"/api/agent/runs/{run_id}/progress",
                         headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["status"] == "completed"
    r = await client.get(f"/api/agent/runs/{run_id}/trace",
                         headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["status"] == "completed"

    # 会话历史：用户原文 + 助手摘要（§5.7）
    r = await client.get(f"/api/agent/sessions/{session_id}/history",
                         headers=_auth(token))
    assert r.status_code == 200
    msgs = r.json()
    assert any(m["role"] == "user" for m in msgs)
    assert any(m["role"] == "assistant" for m in msgs)


# ---------- 跨属主 404 ----------

@pytest.mark.asyncio
async def test_cross_owner_404(client, app_env):
    """run/session 跨属主一律 404（不泄露存在性）。"""
    owner = await _login(client, SAFETY)
    other = await _login(client, RESP)
    r = await client.post("/api/agent/chat", json={"text": "查一下"},
                          headers=_auth(owner))
    run_id, session_id = r.json()["run_id"], r.json()["session_id"]
    await _wait_async(run_id, app_env["db_file"], _TERMINAL)

    assert (await client.get(f"/api/agent/runs/{run_id}/progress",
                             headers=_auth(other))).status_code == 404
    assert (await client.get(f"/api/agent/runs/{run_id}/trace",
                             headers=_auth(other))).status_code == 404
    assert (await client.post(f"/api/agent/runs/{run_id}/confirm",
                              json={"action": "confirm"},
                              headers=_auth(other))).status_code == 404
    assert (await client.post(f"/api/agent/runs/{run_id}/cancel",
                              headers=_auth(other))).status_code == 404
    assert (await client.get(
        f"/api/agent/sessions/{session_id}/history",
        headers=_auth(other))).status_code == 404


# ---------- confirm 202 + cancel ----------

@pytest.mark.asyncio
async def test_confirm_returns_202(client, app_env):
    """副作用计划挂起 → POST confirm 返 202 续跑 → 终态。"""
    from services.agent.run_service import PlanRunService

    app_env["monkeypatch"].setattr(
        PlanRunService, "_CHAT_FACTORY",
        lambda: FakeChatClient(
            {"goal": "建草稿",
             "steps": [{"tool": "pay", "args": {}, "reason": "建"}]}))
    token = await _login(client, SAFETY)
    r = await client.post("/api/agent/chat", json={"text": "建个草稿"},
                          headers=_auth(token))
    run_id = r.json()["run_id"]
    assert await _wait_async(run_id, app_env["db_file"],
                             ("pending_confirm",)) == "pending_confirm"
    assert app_env["pay_calls"]["n"] == 0        # 挂起前副作用零执行

    r = await client.post(f"/api/agent/runs/{run_id}/confirm",
                          json={"action": "confirm"}, headers=_auth(token))
    assert r.status_code == 202, r.text
    assert r.json()["status"] in ("running", *_TERMINAL)

    assert await _wait_async(run_id, app_env["db_file"],
                             _TERMINAL) == "completed"
    assert app_env["pay_calls"]["n"] == 1


@pytest.mark.asyncio
async def test_cancel_endpoint(client, app_env):
    """挂起中取消：端点返 200 + cancelled，副作用零执行。"""
    from services.agent.run_service import PlanRunService

    app_env["monkeypatch"].setattr(
        PlanRunService, "_CHAT_FACTORY",
        lambda: FakeChatClient(
            {"goal": "建草稿",
             "steps": [{"tool": "pay", "args": {}, "reason": "建"}]}))
    token = await _login(client, SAFETY)
    r = await client.post("/api/agent/chat", json={"text": "建个草稿"},
                          headers=_auth(token))
    run_id = r.json()["run_id"]
    assert await _wait_async(run_id, app_env["db_file"],
                             ("pending_confirm",)) == "pending_confirm"

    r = await client.post(f"/api/agent/runs/{run_id}/cancel",
                          headers=_auth(token))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "cancelled"
    assert app_env["pay_calls"]["n"] == 0


# ---------- 空文本契约：新端点快路径侧保留（§5.11） ----------

@pytest.mark.asyncio
async def test_empty_text_todo_contract_on_agent_chat(client):
    """空文本=最新待办清单：/agent/chat 与旧端点行为一致，同步直返旧结构。"""
    token = await _login(client, SAFETY)
    r = await client.post("/api/agent/chat", json={"text": ""},
                          headers=_auth(token))
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["action"] == "order_list"
    assert "data" in data                        # 旧 ChatRoute 结构，非 run_id


# ---------- v2.2 对话窗口：会话管理 / 附件 / 能力 / TTS / 工单问答 ----------

@pytest.mark.asyncio
async def test_session_crud_lifecycle(client):
    """新建→列表→改名→归档→归档视图→删除（物理删，历史随之 404）。"""
    token = await _staff_token(client)
    r = await client.post("/api/agent/sessions", json={"title": "安全问询"},
                          headers=_auth(token))
    assert r.status_code == 200
    sid = r.json()["id"]
    r = await client.get("/api/agent/sessions", headers=_auth(token))
    assert r.status_code == 200 and any(s["id"] == sid for s in r.json())
    r = await client.patch(f"/api/agent/sessions/{sid}",
                           json={"title": "动火问询", "archived": True},
                           headers=_auth(token))
    assert r.status_code == 200
    active = (await client.get("/api/agent/sessions",
                               headers=_auth(token))).json()
    assert all(s["id"] != sid for s in active)
    archived = (await client.get("/api/agent/sessions",
                                 params={"archived_only": "true"},
                                 headers=_auth(token))).json()
    assert any(s["id"] == sid and s["title"] == "动火问询" for s in archived)
    r = await client.delete(f"/api/agent/sessions/{sid}", headers=_auth(token))
    assert r.status_code == 200
    r = await client.get(f"/api/agent/sessions/{sid}/history",
                         headers=_auth(token))
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_session_cross_owner_404(client):
    """safety 的会话，lisi 改名/删除/查历史一律 404（不泄露存在性）。"""
    s_token = await _login(client, SAFETY)
    r_token = await _login(client, RESP)
    sid = (await client.post("/api/agent/sessions", json={},
                             headers=_auth(s_token))).json()["id"]
    assert (await client.patch(f"/api/agent/sessions/{sid}",
                               json={"title": "偷改"},
                               headers=_auth(r_token))).status_code == 404
    assert (await client.delete(f"/api/agent/sessions/{sid}",
                                headers=_auth(r_token))).status_code == 404
    assert (await client.get(f"/api/agent/sessions/{sid}/history",
                             headers=_auth(r_token))).status_code == 404


@pytest.mark.asyncio
async def test_upload_attachment_and_chat_flow(client):
    """附件上传→chat 携带附件强制认知路径→run/消息落附件（越界路径 400）。"""
    token = await _staff_token(client)
    r = await client.post("/api/agent/uploads",
                          files={"file": ("site.png", _png(), "image/png")},
                          headers=_auth(token))
    assert r.status_code == 200, r.text
    path = r.json()["path"]
    assert path.startswith("data/uploads/chat/")
    # 魔数伪装必须拒
    bad = await client.post("/api/agent/uploads",
                            files={"file": ("evil.png",
                                            b"GIF89a" + b"0" * 32,
                                            "image/png")},
                            headers=_auth(token))
    assert bad.status_code == 400
    # 越界路径直接 400（穿越/不存在/扩展名）
    for evil in (["../etc/passwd.png"], ["data/uploads/none.png"],
                 ["note.txt" ] ):
        r = await client.post("/api/agent/chat",
                              json={"text": "分析附件", "attachments": evil},
                              headers=_auth(token))
        assert r.status_code == 400, evil
    # 合法附件 → 认知 run 创建，消息/附件留痕
    r = await client.post("/api/agent/chat",
                          json={"text": "帮我分析这张现场照片",
                                "attachments": [path]},
                          headers=_auth(token))
    assert r.status_code == 200 and r.json()["path"] == "cognitive"
    sid = r.json()["session_id"]
    hist = (await client.get(f"/api/agent/sessions/{sid}/history",
                             headers=_auth(token))).json()
    assert any(m["role"] == "user" and m.get("attachments")
               for m in hist)


def test_bind_attachments_server_override():
    """内核附件强制绑定：LLM 编的 video/images 一律被服务端清单覆盖。"""
    from services.agent.kernel import PlanExecutor
    atts = ["data/uploads/chat/a.mp4", "data/uploads/chat/b.png",
            "data/uploads/chat/c.jpg"]
    out = PlanExecutor._bind_attachments(
        "run_video_pipeline",
        {"video": "hacked.mp4", "images": ["hacked.png"], "mode": "full"},
        atts)
    assert out["video"] == "data/uploads/chat/a.mp4"
    assert set(out["images"]) == {"data/uploads/chat/b.png",
                                  "data/uploads/chat/c.jpg"}
    assert out["mode"] == "full"          # 非附件参数不动
    other = PlanExecutor._bind_attachments("rag_search", {"query": "x"}, atts)
    assert other == {"query": "x"}        # 非视频工具不受影响


@pytest.mark.asyncio
async def test_model_info_and_tts_unconfigured(client, monkeypatch):
    # 显式注入 tts 未配置态（统一云端通道就绪时 tts 可用属正常态）
    import core.config as cc

    orig = cc.ConfigLoader.load
    monkeypatch.setattr(cc.ConfigLoader, "load",
                        lambda self: {**orig(self), "tts": {}})
    token = await _staff_token(client)
    r = await client.get("/api/agent/model-info", headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    assert {"provider_available", "providers",
            "asr_available", "tts_available"} <= set(body)
    r = await client.post("/api/agent/tts", json={"text": "你好"},
                          headers=_auth(token))
    assert r.status_code == 501           # 未配置 tts.* → 能力未拥有


# ---------- v2.2 工单 AI 弹窗（orders/{id}/ask）----------

def _seed_order(db_file: str, assignee_id: str | None) -> str:
    from dao.db import get_conn
    from dao.models import TaskDAO, UserDAO, WorkOrderDAO
    conn = get_conn(db_file)
    try:
        uid = assignee_id or UserDAO(conn).get_by_name("safety")["id"]
        tid = TaskDAO(conn).insert(uid, "{}", source="text")
        wid = WorkOrderDAO(conn).insert(
            tid, "电焊机旁纸箱未清理", "动火作业规范第X条",
            "移除易燃物并配置监火人", "较大", "请立即清理")
        WorkOrderDAO(conn).set_dispatch(wid, assignee_id, "2099-01-01 00:00:00",
                                        "2026-08-30 00:00:00")
        conn.commit()
        return wid
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_order_ask_permissions_and_degrade(client, app_env):
    """本单责任人可问（LLM 未配置→200 降级提示）；他人 403；未知 404。"""
    from dao.db import get_conn
    from dao.models import UserDAO
    conn = get_conn(app_env["db_file"])
    try:
        lisi_id = UserDAO(conn).get_by_name("lisi")["id"]
    finally:
        conn.close()
    mine = _seed_order(app_env["db_file"], lisi_id)
    other = _seed_order(app_env["db_file"], None)   # 未派发单
    r_token = await _login(client, RESP)
    s_token = await _staff_token(client)

    # 隔离网络：问询走真实 ChatClient 会打配置里的云 provider，测试内替换
    class _NoProvider:
        def available_provider(self):
            return None

    app_env["monkeypatch"].setattr("core.chat_client.get_chat_client",
                                   lambda: _NoProvider())

    r = await client.post(f"/api/orders/{mine}/ask",
                          json={"question": "这条单的规范依据是什么？"},
                          headers=_auth(r_token))
    assert r.status_code == 200
    assert r.json()["status"] == "failed"   # 测试环境无 LLM provider → 可读降级
    assert "不可用" in r.json()["answer"]

    r = await client.post(f"/api/orders/{other}/ask",
                          json={"question": "要求是什么？"},
                          headers=_auth(r_token))
    assert r.status_code == 403             # 非本单责任人

    r = await client.post("/api/orders/none_exist/ask",
                          json={"question": "在吗"}, headers=_auth(s_token))
    assert r.status_code == 404

    r = await client.post(f"/api/orders/{mine}/ask",
                          json={"question": "验收要看什么？"},
                          headers=_auth(s_token))
    assert r.status_code == 200             # admin/safety 可问任意单


# ---------- v2.2 上下文管理与会话记忆 ----------

class _CapturingFake(FakeChatClient):
    """在 FakeChatClient 基础上捕获规划调用的 user 消息。"""

    plan_user_texts: list = []

    def __init__(self):
        super().__init__({"goal": "测试",
                          "steps": [{"tool": "echo", "args": {},
                                     "reason": "查"}]})

    def chat(self, system, user, *, json_schema=None, max_tokens=1024,
             total_deadline_sec=30.0, provider=None):
        if json_schema is not None:
            type(self).plan_user_texts.append(user)
        return super().chat(system, user, json_schema=json_schema,
                            max_tokens=max_tokens,
                            total_deadline_sec=total_deadline_sec,
                            provider=provider)


@pytest.mark.asyncio
async def test_context_turns_and_cross_session_memory(client, app_env):
    """规划上下文工程：本会话最近轮原文 + 跨会话记忆要点均注入规划输入。"""
    from services.agent.run_service import PlanRunService
    token = await _staff_token(client)

    # 捕获型 Fake（替换 app_env 的默认 Fake 工厂）
    _CapturingFake.plan_user_texts = []
    app_env["monkeypatch"].setattr(PlanRunService, "_CHAT_FACTORY", _CapturingFake)

    from dao.db import get_conn
    from dao.models import AgentChatDAO

    # 会话 A（历史会话）：原文轮次 + 要点记忆
    conn = get_conn(app_env["db_file"])
    try:
        dao = AgentChatDAO(conn)
        user_id = dao.conn.execute(
            "SELECT id FROM users WHERE username='safety'").fetchone()["id"]
        sess_a = dao.create_session(user_id, "历史会话A")
        dao.insert_message(sess_a, "user", "查一下3号工单的进度")
        dao.insert_message(sess_a, "assistant", "3号工单已派发给张三，截止明天 18:00",
                           digest="1.echo(success) 查询3号工单进度")
    finally:
        conn.close()

    # 会话 B：预置一轮原文，再发起认知 run
    r = await client.post("/api/agent/sessions", json={"title": "当前会话B"},
                          headers=_auth(token))
    sess_b = r.json()["id"]
    conn = get_conn(app_env["db_file"])
    try:
        dao = AgentChatDAO(conn)
        dao.insert_message(sess_b, "user", "刚才说的整改要求有哪些")
        dao.insert_message(sess_b, "assistant", "整改要求是清理易燃物并配监火人")
    finally:
        conn.close()

    r = await client.post("/api/agent/chat", json={
        "text": "继续", "session_id": sess_b}, headers=_auth(token))
    assert r.status_code == 200, f"chat={r.status_code} {r.text[:150]}"
    assert r.json()["path"] == "cognitive", r.text[:150]
    run_id = r.json()["run_id"]
    status = await _wait_async(run_id, app_env["db_file"], _TERMINAL)
    assert status in ("completed", "degraded"), status

    plan_user = chr(10).join(_CapturingFake.plan_user_texts)
    # 会话内原文轮次注入（理解指代）
    assert "刚才说的整改要求有哪些" in plan_user
    assert "整改要求是清理易燃物并配监火人" in plan_user
    # 跨会话记忆注入（其他会话要点，标注非本轮指令）
    assert "长期记忆" in plan_user
    assert "查一下3号工单的进度" in plan_user or "要点" in plan_user
    # 本轮请求仍在末尾
    assert "本轮用户请求: 继续" in plan_user


@pytest.mark.asyncio
async def test_memory_disabled_by_config(client, app_env):
    """agent.memory_enabled=false 时规划输入不含长期记忆段。"""
    from services.agent.run_service import PlanRunService
    token = await _staff_token(client)
    _CapturingFake.plan_user_texts = []
    app_env["monkeypatch"].setattr(PlanRunService, "_CHAT_FACTORY", _CapturingFake)

    from dao.db import get_conn
    from dao.models import AgentChatDAO

    conn = get_conn(app_env["db_file"])
    try:
        dao = AgentChatDAO(conn)
        user_id = dao.conn.execute(
            "SELECT id FROM users WHERE username='safety'").fetchone()["id"]
        sess_a = dao.create_session(user_id, "历史会话")
        dao.insert_message(sess_a, "assistant", "", digest="跨会话要点Y")
        sess_b = dao.create_session(user_id, "当前会话")
    finally:
        conn.close()

    from core.config import ConfigLoader as _CL
    loader = _CL()
    orig_get = loader.get

    def _get(self, key, *a, **k):
        if key == "agent":
            return {"memory_enabled": False}
        return orig_get(key, *a, **k)

    app_env["monkeypatch"].setattr(_CL, "get", _get)

    r = await client.post("/api/agent/chat", json={
        "text": "继续", "session_id": sess_b}, headers=_auth(token))
    assert r.status_code == 200
    run_id = r.json()["run_id"]
    status = await _wait_async(run_id, app_env["db_file"], _TERMINAL)
    assert status in ("completed", "degraded"), status

    plan_user = chr(10).join(_CapturingFake.plan_user_texts)
    assert "长期记忆" not in plan_user
    assert "跨会话要点Y" not in plan_user


@pytest.mark.asyncio
async def test_greeting_fast_path(client):
    """问候/寒暄走规则快路径零 LLM 直答（不建会话、不建认知 run）。"""
    token = await _staff_token(client)
    for q in ("你好，你是谁", "你是干什么的？", "嗨"):
        r = await client.post("/api/agent/chat", json={"text": q},
                              headers=_auth(token))
        assert r.status_code == 200, r.text[:120]
        body = r.json()
        assert body.get("action") == "greeting", (q, body)
        assert "安全助手" in body.get("hint", "")
    # 非问候不被误伤
    r = await client.post("/api/agent/chat", json={"text": "帮我写一份周报"},
                          headers=_auth(token))
    assert r.json().get("action") != "greeting"


@pytest.mark.asyncio
async def test_delete_session_with_children(client, app_env):
    """删除含消息/认知 run 的会话：级联成功且子表清空（外键顺序回归）。"""
    from dao.db import get_conn
    from dao.models import AgentChatDAO
    token = await _staff_token(client)
    sid = (await client.post("/api/agent/sessions", json={},
                             headers=_auth(token))).json()["id"]
    # 种两条消息 + 一个 run（含一步）
    conn = get_conn(app_env["db_file"])
    try:
        dao = AgentChatDAO(conn)
        rid = dao.create_run(sid, dao.get_session(sid)["user_id"], "测试")
        dao.insert_message(sid, "user", "测试消息", run_id=rid)
        dao.insert_step(rid, 0, "echo", status="success")
        conn.commit()
    finally:
        conn.close()

    r = await client.delete(f"/api/agent/sessions/{sid}", headers=_auth(token))
    assert r.status_code == 200, r.text

    conn = get_conn(app_env["db_file"])
    try:
        for sql, label in (
            ("SELECT COUNT(*) FROM chat_messages WHERE session_id=?", "messages"),
            ("SELECT COUNT(*) FROM agent_chat_runs WHERE session_id=?", "runs"),
            ("SELECT COUNT(*) FROM agent_chat_run_steps WHERE run_id IN "
             "(SELECT id FROM agent_chat_runs WHERE session_id=?)", "steps"),
        ):
            n = conn.execute(sql, (sid,)).fetchone()[0]
            assert n == 0, f"{label} 未级联删除"
    finally:
        conn.close()
