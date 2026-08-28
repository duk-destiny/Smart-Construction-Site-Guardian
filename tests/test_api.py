"""Phase 2 API 全端点测试：httpx AsyncClient 直连 ASGI（无网络栈）。

覆盖（重构提示词 Phase 2 验收口径）：
- 每个端点正常路径；
- 权限拒绝路径：safety 访问 admin 端点 403、responsible 上报 403、
  无/过期/伪造 token 401；
- 上传端点负向用例：魔数不符、扩展名与内容不一致、超限大小（注入 0MB 配置）。

隔离：每例 monkeypatch dao.db.DEFAULT_DB_PATH 到临时库并补种默认账号，
互不污染；登录限速窗口字典按例清空。API_PREWARM=0 关闭启动预热。
"""
from __future__ import annotations

import asyncio
import os
import time
from urllib.parse import quote

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# 种子账号（core/bootstrap._DEFAULT_USERS 同源）
ADMIN = ("admin", "admin123")
SAFETY = ("safety", "demo1234")
RESP = ("lisi", "demo1234")

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _png() -> bytes:
    return PNG_MAGIC + b"0" * 64


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def app_env(tmp_path, monkeypatch):
    """临时库 + 种子账号 + 关预热的 app（ASGITransport 不触发 lifespan，故在此自举）。"""
    import dao.db as dao_db
    from services import auth_service

    monkeypatch.setattr(dao_db, "DEFAULT_DB_PATH", str(tmp_path / "api_test.db"))
    auth_service._FAILS.clear()
    monkeypatch.setenv("API_PREWARM", "0")
    from core.bootstrap import ensure_initialized

    ensure_initialized()
    from api.main import create_app

    return create_app()


@pytest_asyncio.fixture
async def client(app_env):
    transport = ASGITransport(app=app_env)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


async def _login(client: AsyncClient, who: tuple[str, str]) -> str:
    r = await client.post("/api/auth/login",
                          json={"username": who[0], "password": who[1]})
    assert r.status_code == 200, r.text
    return r.json()["token"]


async def _staff_token(client: AsyncClient) -> str:
    return await _login(client, SAFETY)


async def _create_text_hazard(client: AsyncClient) -> dict:
    """safety 建文字隐患单，返回任务结果（含 task_id / work_order）。"""
    token = await _staff_token(client)
    r = await client.post("/api/tasks/text", json={
        "description": "3号楼西侧电焊机旁堆着纸箱没人清理",
        "hazard_key": "flammable", "scene_id": "hot_work",
        "location": "3号楼西侧",
    }, headers=_auth(token))
    assert r.status_code == 200, r.text
    return {"token": token, **r.json()}


# ---------- meta / auth ----------

@pytest.mark.asyncio
async def test_healthz(client):
    r = await client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_login_success_and_mcp_flag(client):
    r = await client.post("/api/auth/login",
                          json={"username": ADMIN[0], "password": ADMIN[1]})
    body = r.json()
    assert r.status_code == 200
    assert body["role"] == "admin"
    # 种子账号带初始密码标记：登录响应必须携带，供前端做首登改密门控
    assert body["must_change_password"] is True
    assert body["expires_in"] > 0


@pytest.mark.asyncio
async def test_login_wrong_password_unified_message(client):
    r = await client.post("/api/auth/login",
                          json={"username": ADMIN[0], "password": "nope"})
    assert r.status_code == 401
    assert r.json()["detail"] == "用户名或密码错误"


@pytest.mark.asyncio
async def test_me_requires_and_returns_role(client):
    assert (await client.get("/api/auth/me")).status_code == 401
    assert (await client.get("/api/auth/me",
                             headers=_auth("garbage"))).status_code == 401
    token = await _login(client, ADMIN)
    r = await client.get("/api/auth/me", headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["role"] == "admin"


@pytest.mark.asyncio
async def test_expired_token_rejected(client):
    import jwt

    from api.deps import _jwt_secret
    expired = jwt.encode({"sub": "u_admin", "exp": int(time.time()) - 10},
                         _jwt_secret(), algorithm="HS256")
    r = await client.get("/api/auth/me", headers=_auth(expired))
    assert r.status_code == 401
    assert "过期" in r.json()["detail"]


@pytest.mark.asyncio
async def test_change_password_flow(client):
    token = await _login(client, SAFETY)
    r = await client.post("/api/auth/change-password", json={
        "old_password": SAFETY[1], "new_password": "newsafe2026"},
        headers=_auth(token))
    assert r.status_code == 200, r.text
    # 旧密码失效、新密码可登录、改密标记清除
    old = await client.post("/api/auth/login", json={
        "username": SAFETY[0], "password": SAFETY[1]})
    assert old.status_code == 401
    new = await client.post("/api/auth/login", json={
        "username": SAFETY[0], "password": "newsafe2026"})
    assert new.status_code == 200
    assert new.json()["must_change_password"] is False


# ---------- RBAC ----------

@pytest.mark.asyncio
async def test_safety_cannot_access_admin(client):
    token = await _staff_token(client)
    r = await client.get("/api/admin/users", headers=_auth(token))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_responsible_cannot_upload(client):
    token = await _login(client, RESP)
    r = await client.post("/api/tasks/text", json={
        "description": "x", "hazard_key": "flammable"},
        headers=_auth(token))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_responsible_cannot_list_all_orders(client):
    token = await _login(client, RESP)
    r = await client.get("/api/orders", headers=_auth(token))
    assert r.status_code == 403


# ---------- 上报 / 研判 ----------

@pytest.mark.asyncio
async def test_upload_media_and_magic_guard(client):
    token = await _staff_token(client)
    permit = '{"scene": "hot_work", "fire_level": "一级", "area": "3号楼"}'
    r = await client.post("/api/tasks/media",
                          files={"file": ("shot.png", _png(), "image/png")},
                          data={"scene_id": "hot_work", "permit_info": permit,
                                "auto_run": "false"},
                          headers=_auth(token))
    body = r.json()
    assert r.status_code == 200, r.text
    assert body["task_id"].startswith("t_")
    assert body["media_path"].startswith("data/uploads/")
    assert body["async_started"] is False

    # 负向：内容为 GIF 的 ".png" —— 魔数校验必须拒绝
    bad = await client.post("/api/tasks/media",
                            files={"file": ("evil.png", b"GIF89a" + b"0" * 32,
                                            "image/png")},
                            data={"scene_id": "hot_work"},
                            headers=_auth(token))
    assert bad.status_code == 400
    assert "魔数" in bad.json()["detail"]


@pytest.mark.asyncio
async def test_upload_size_limit(client, tmp_path, monkeypatch):
    """注入 max_image_mb=0 的临时配置：任何非空图片都超限。"""
    import yaml

    from core import config as core_config
    cfg = tmp_path / "tiny.yaml"
    cfg.write_text(yaml.safe_dump({
        "upload": {"max_image_mb": 0, "max_video_mb": 200, "max_pdf_mb": 20}}),
        encoding="utf-8")
    monkeypatch.setattr(core_config, "_SHARED",
                        {"config/config.yaml": core_config.ConfigLoader(str(cfg))})
    token = await _staff_token(client)
    r = await client.post("/api/tasks/media",
                          files={"file": ("shot.png", _png(), "image/png")},
                          data={"scene_id": "hot_work"},
                          headers=_auth(token))
    assert r.status_code == 400
    assert "上限" in r.json()["detail"]


@pytest.mark.asyncio
async def test_text_hazard_and_unknown_key(client):
    res = await _create_text_hazard(client)
    assert res["task_id"]
    assert res["risk_level"] == "一般"
    assert res["work_order"]["hazard_desc"]

    token = await _staff_token(client)
    r = await client.post("/api/tasks/text", json={
        "description": "编造类别", "hazard_key": "not_in_whitelist"},
        headers=_auth(token))
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_async_run_result_polling(app_env, client, monkeypatch):
    """后台研判 + 结果轮询：注入 stub Orchestrator（同 test_async_run 口径）。"""

    class FakeOrch:
        def __init__(self, *a, **k):
            self.action = None

        def execute(self, task_id, images=None, permit_info=None):
            time.sleep(0.2)

            class _R:
                payload = {
                    "vision": {"payload": {"detections": []}},
                    "rule": {"payload": {"compliance": []}},
                    "fusion": {"payload": {"risk_level": "一般", "reasons": []}},
                    "action": {"payload": {"work_order": {
                        "risk_level": "一般", "hazard_desc": "异步样本",
                        "clause": "", "requirement": "整改"}}},
                    "review": {"payload": {"needs_review": False}},
                }

                def to_dict(self):
                    return {"status": "success", "payload": self.payload}

            return _R()

    from services.task_service import TaskService

    monkeypatch.setattr(TaskService, "_ORCH_FACTORY", FakeOrch)
    token = await _staff_token(client)
    made = await client.post("/api/tasks/media",
                             files={"file": ("shot.png", _png(), "image/png")},
                             data={"scene_id": "hot_work", "auto_run": "false"},
                             headers=_auth(token))
    tid = made.json()["task_id"]
    r = await client.post(f"/api/tasks/{tid}/run",
                          json={"images": [made.json()["media_path"]],
                                "permit_info": {"scene": "hot_work"}},
                          headers=_auth(token))
    assert r.status_code == 200, r.text

    deadline = time.time() + 10
    result = None
    while time.time() < deadline:
        rr = await client.get(f"/api/tasks/{tid}/result", headers=_auth(token))
        if rr.status_code == 200:
            result = rr.json()
            break
        await asyncio.sleep(0.1)
    assert result and result["status"] == "success"
    # 取后即清：第二次轮询 404
    assert (await client.get(f"/api/tasks/{tid}/result",
                             headers=_auth(token))).status_code == 404
    # 工单已落库 → 台账可见
    orders = await client.get("/api/tasks", headers=_auth(token))
    assert any(row["task_id"] == tid for row in orders.json())


@pytest.mark.asyncio
async def test_progress_owner_isolation(client):
    from services import db as sdb
    from services.task_service import TaskService

    res = await _create_text_hazard(client)
    tid = res["task_id"]
    sdb.call(lambda conn: TaskService(conn).update_progress(
        tid, "vision", "success", 5))
    safety_token = res["token"]
    own = await client.get(f"/api/tasks/{tid}/progress",
                           headers=_auth(safety_token))
    assert own.status_code == 200
    assert own.json()["vision"]["status"] == "success"
    # 非属主视角恒为空（属主隔离由服务层保证）
    resp_token = await _login(client, RESP)
    other = await client.get(f"/api/tasks/{tid}/progress",
                             headers=_auth(resp_token))
    assert other.status_code == 200
    assert other.json() == {}


@pytest.mark.asyncio
async def test_task_detail_404_and_shape(client):
    token = await _staff_token(client)
    assert (await client.get("/api/tasks/t_missing/detail",
                             headers=_auth(token))).status_code == 404
    res = await _create_text_hazard(client)
    r = await client.get(f"/api/tasks/{res['task_id']}/detail",
                         headers=_auth(token))
    body = r.json()
    assert r.status_code == 200
    assert body["task"]["id"] == res["task_id"]
    assert body["risk"]["risk_level"] == "一般"
    assert "detections" in body and "compliances" in body


@pytest.mark.asyncio
async def test_override_flow(client):
    res = await _create_text_hazard(client)
    token = await _staff_token(client)
    r = await client.post(f"/api/tasks/{res['task_id']}/override",
                          json={"new_level": "较大", "reason": "复核上调"},
                          headers=_auth(token))
    assert r.status_code == 200, r.text
    detail = await client.get(f"/api/tasks/{res['task_id']}/detail",
                              headers=_auth(token))
    assert detail.json()["risk"]["override_level"] == "较大"


@pytest.mark.asyncio
async def test_query_chat_readonly(client):
    await _create_text_hazard(client)
    token = await _staff_token(client)
    r = await client.post("/api/tasks/query-chat",
                          json={"text": "近7天有多少张未闭环工单"},
                          headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["action"] in {
        "order_detail", "order_list", "overdue_stats", "weekly_stats",
        "confirm_list", "unknown"}


# ---------- 告警 ----------

def _create_alarm() -> str:
    from services import db as sdb
    from services.task_service import TaskService

    return sdb.call(lambda conn: TaskService(conn).create_alarm_event(
        session_id="api-test", task_id=None, scene_id="hot_work",
        cls="spark", conf=0.93, source="api-test", force=True))


@pytest.mark.asyncio
async def test_alarm_flow_list_detail_convert_status(client):
    aid = _create_alarm()
    token = await _login(client, ADMIN)
    lst = await client.get("/api/alarms", headers=_auth(token))
    assert lst.status_code == 200
    assert any(a["id"] == aid for a in lst.json())

    detail = await client.get(f"/api/alarms/{aid}", headers=_auth(token))
    assert detail.status_code == 200
    assert detail.json()["cls"] == "spark"

    conv = await client.post(f"/api/alarms/{aid}/convert-order",
                             headers=_auth(token))
    assert conv.status_code == 200, conv.text
    assert conv.json()["order_id"].startswith("w_")
    # 幂等守卫：同一告警二次转换 400
    again = await client.post(f"/api/alarms/{aid}/convert-order",
                              headers=_auth(token))
    assert again.status_code == 400

    patch = await client.patch(f"/api/alarms/{aid}/status",
                               json={"status": "false_alarm"},
                               headers=_auth(token))
    assert patch.status_code == 200
    after = await client.get(f"/api/alarms/{aid}", headers=_auth(token))
    assert after.json()["status"] == "false_alarm"


# ---------- 工单闭环 ----------

@pytest.mark.asyncio
async def test_orders_full_loop(client):
    res = await _create_text_hazard(client)
    tid, safety_token = res["task_id"], res["token"]
    admin_token = await _login(client, ADMIN)

    lst = await client.get("/api/orders", headers=_auth(safety_token))
    assert any(o["task_id"] == tid for o in lst.json())

    panel = await client.get(f"/api/orders/by-task/{tid}/panel",
                             headers=_auth(admin_token))
    assert panel.status_code == 200
    assert "lisi" in panel.json()["responsible_names"]

    disp = await client.post(f"/api/orders/by-task/{tid}/dispatch",
                             json={"assignee": "lisi", "hours": 24},
                             headers=_auth(admin_token))
    assert disp.status_code == 200, disp.text
    oid = panel.json()["order"]["id"]

    resp_token = await _login(client, RESP)
    mine = await client.get("/api/orders/mine", headers=_auth(resp_token))
    assert any(o["id"] == oid and o["status"] == "open" for o in mine.json())

    rect = await client.post(
        f"/api/orders/{oid}/rectification",
        data={"note": "纸箱已清理，现场恢复"},
        files=[("photos", ("fix.png", _png(), "image/png"))],
        headers=_auth(resp_token))
    assert rect.status_code == 200, rect.text

    pending = await client.get("/api/orders/pending-review",
                               headers=_auth(admin_token))
    assert any(o["id"] == oid for o in pending.json())

    review = await client.post(f"/api/orders/{oid}/review",
                               json={"approve": True},
                               headers=_auth(admin_token))
    assert review.status_code == 200, review.text
    # 责任人视图默认排除已闭环（list_by_assignee 口径）——
    # closed 状态经管理侧台账确认
    mine2 = await client.get("/api/orders/mine", headers=_auth(resp_token))
    assert not any(o["id"] == oid for o in mine2.json())
    ledger = await client.get("/api/orders", headers=_auth(admin_token))
    assert any(o["id"] == oid and o["status"] == "closed"
               for o in ledger.json())


@pytest.mark.asyncio
async def test_export_download_and_traversal_guard(client):
    res = await _create_text_hazard(client)
    admin_token = await _login(client, ADMIN)
    panel = await client.get(f"/api/orders/by-task/{res['task_id']}/panel",
                             headers=_auth(admin_token))
    oid = panel.json()["order"]["id"]

    exp = await client.post(f"/api/orders/{oid}/export",
                            headers=_auth(admin_token))
    assert exp.status_code == 200, exp.text
    name = exp.json()["file"]["name"]
    assert name.endswith(".xlsx")

    dl = await client.get(exp.json()["file"]["download_url"],
                          headers=_auth(admin_token))
    assert dl.status_code == 200
    assert dl.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    # 路径穿越必须被拒（400 校验拒绝或 404 路由不匹配，均不可达）
    trav = await client.get(f"/api/reports/exports/{quote('../app.db', safe='')}",
                            headers=_auth(admin_token))
    assert trav.status_code in (400, 404)
    missing = await client.get("/api/reports/exports/不存在.xlsx",
                               headers=_auth(admin_token))
    assert missing.status_code == 404


# ---------- 周报 ----------

@pytest.mark.asyncio
async def test_weekly_report_generate_download_preview(client):
    await _create_text_hazard(client)
    token = await _login(client, ADMIN)
    r = await client.post("/api/reports/weekly",
                          json={"start": "2026-08-01", "end": "2026-08-28"},
                          headers=_auth(token))
    body = r.json()
    assert r.status_code == 200, r.text
    assert body["stats"]["orders_total"] >= 1
    assert body["file"]["name"].endswith(".pdf")

    dl = await client.get(body["file"]["download_url"], headers=_auth(token))
    assert dl.status_code == 200
    assert dl.content[:5] == b"%PDF-"

    preview = await client.get("/api/reports/weekly/preview",
                               headers=_auth(token))
    assert preview.status_code == 200
    assert "orders_by_status" in preview.json()


# ---------- 管理端 ----------

@pytest.mark.asyncio
async def test_admin_user_lifecycle(client):
    token = await _login(client, ADMIN)
    created = await client.post("/api/admin/users", json={
        "username": "wang5", "password": "demopass123",
        "role": "responsible"}, headers=_auth(token))
    assert created.status_code == 200, created.text

    users = await client.get("/api/admin/users", headers=_auth(token))
    row = next(u for u in users.json() if u["username"] == "wang5")
    assert "pwd_hash" not in row

    first_login = await client.post("/api/auth/login", json={
        "username": "wang5", "password": "demopass123"})
    assert first_login.status_code == 200
    wang_token = first_login.json()["token"]

    reset = await client.post(
        f"/api/admin/users/{row['id']}/reset-password",
        json={"new_password": "brandnew456"}, headers=_auth(token))
    assert reset.status_code == 200
    assert (await client.post("/api/auth/login", json={
        "username": "wang5", "password": "demopass123"})).status_code == 401

    disabled = await client.post(
        f"/api/admin/users/{row['id']}/disabled",
        json={"disabled": True}, headers=_auth(token))
    assert disabled.status_code == 200
    assert (await client.post("/api/auth/login", json={
        "username": "wang5", "password": "brandnew456"})).status_code == 401
    # 已签发 token 在停用后立即失效（me 依赖 DB 复核）
    me = await client.get("/api/auth/me", headers=_auth(wang_token))
    assert me.status_code == 401

    enabled = await client.post(
        f"/api/admin/users/{row['id']}/disabled",
        json={"disabled": False}, headers=_auth(token))
    assert enabled.status_code == 200
    assert (await client.post("/api/auth/login", json={
        "username": "wang5", "password": "brandnew456"})).status_code == 200


@pytest.mark.asyncio
async def test_admin_models_list_switch_and_invalid(client, monkeypatch):
    from services import admin_console as ac

    monkeypatch.setattr("api.routers.admin._reload_running_engines",
                        lambda: None)
    token = await _login(client, ADMIN)
    ac.register_model(name="fire", version="api-test",
                      path="data/models/yolov8_fire_smoke_v2.onnx",
                      data_yaml=None, imgsz=640, mAP50=0.5, mAP50_95=0.4,
                      notes=None, user_id="u_admin")
    listing = await client.get("/api/admin/models", headers=_auth(token))
    assert listing.status_code == 200
    target = next(m for m in listing.json()["models"]
                  if m["version"] == "api-test")

    switch = await client.post("/api/admin/models/switch", json={
        "name": "fire", "model_id": target["id"]}, headers=_auth(token))
    assert switch.status_code == 200
    assert switch.json()["active"]["id"] == target["id"]

    bad = await client.post("/api/admin/models/switch", json={
        "name": "fire", "model_id": "m_not_exist"}, headers=_auth(token))
    assert bad.status_code == 400


@pytest.mark.asyncio
async def test_admin_kb_import_bad_magic(client):
    token = await _login(client, ADMIN)
    r = await client.post("/api/admin/kb/import",
                          files={"file": ("spec.pdf", _png(), "application/pdf")},
                          headers=_auth(token))
    assert r.status_code == 400
    assert "不一致" in r.json()["detail"]


@pytest.mark.asyncio
async def test_admin_self_check_and_lists(client):
    token = await _login(client, ADMIN)
    check = await client.post("/api/admin/self-check", headers=_auth(token))
    assert check.status_code == 200
    items = check.json()["items"]
    assert len(items) == 3
    db_item = next(i for i in items if i["item"] == "数据库")
    assert db_item["ok"] is True

    for path in ("/api/admin/audit", "/api/admin/feedback",
                 "/api/admin/eval", "/api/admin/hazard-summary",
                 "/api/admin/notification-logs", "/api/admin/mock-capture",
                 "/api/admin/kb/docs", "/api/admin/notify/status"):
        r = await client.get(path, headers=_auth(token))
        assert r.status_code == 200, f"{path}: {r.text}"

    audit_csv = await client.get("/api/admin/audit/export",
                                 headers=_auth(token))
    assert audit_csv.status_code == 200
    assert "text/csv" in audit_csv.headers["content-type"]
    fb_csv = await client.get("/api/admin/feedback/export",
                              headers=_auth(token))
    assert fb_csv.status_code == 200


@pytest.mark.asyncio
async def test_clear_data_guard(client):
    token = await _login(client, ADMIN)
    wrong = await client.post("/api/admin/data/clear",
                              json={"confirmation": "reset"},
                              headers=_auth(token))
    assert wrong.status_code == 400
    ok = await client.post("/api/admin/data/clear",
                           json={"confirmation": "RESET"},
                           headers=_auth(token))
    assert ok.status_code == 200
    assert "deleted" in ok.json()


# ---------- CORS / WebSocket ----------

@pytest.mark.asyncio
async def test_cors_only_in_dev_mode(app_env, monkeypatch):
    origin = {"Origin": "http://localhost:5173",
              "Access-Control-Request-Method": "POST"}
    async with AsyncClient(transport=ASGITransport(app=app_env),
                           base_url="http://testserver") as c:
        pre = await c.options("/api/auth/login", headers=origin)
        assert "access-control-allow-origin" not in {
            k.lower() for k in pre.headers}

    monkeypatch.setenv("API_DEV_CORS", "1")
    from api.main import create_app
    dev_app = create_app()
    async with AsyncClient(transport=ASGITransport(app=dev_app),
                           base_url="http://testserver") as c:
        pre2 = await c.options("/api/auth/login", headers=origin)
        assert pre2.headers.get("access-control-allow-origin") \
            == "http://localhost:5173"


def test_ws_realtime_broadcast(app_env, monkeypatch):
    """Phase 4：Hub 运行时 WS 推帧（hello→frame→ping/pong）；未启用报告不可用。"""
    import tempfile

    from api.realtime_hub import RealtimeHub
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from api.deps import create_access_token

    token, _ = create_access_token("u_admin", "admin", "admin")
    with TestClient(app_env) as tc:
        # Hub 未启动 → accept 后报告 unavailable 并关闭
        with tc.websocket_connect(
                f"/api/ws/realtime?token={quote(token, safe='')}") as ws:
            hello = ws.receive_json()
            assert hello["type"] == "unavailable"

        # 注入 stub Hub：发布一帧后连接应收到该帧
        class StubEngine:
            def analyze(self, frame, source_key="default"):
                return [], {"status": "合规", "level": "safe",
                            "violations": [], "safe": []}

            def draw(self, frame, comp):
                return frame

        hub = RealtimeHub(["demo://"], engine=StubEngine())
        hub.start()
        monkeypatch.setattr("api.routers.ws.get_hub", lambda: hub)
        try:
            hub.cycle()  # 手动发布一帧（不依赖后台线程时序）
            with tc.websocket_connect(
                    f"/api/ws/realtime?token={quote(token, safe='')}") as ws:
                hello = ws.receive_json()
                assert hello["type"] == "hello"
                assert hello["sources"][0]["source"].startswith("demo://")
                frame = ws.receive_json()
                assert frame["type"] == "frame"
                assert frame["jpeg"] and frame["level"] == "safe"
                ws.send_text("ping")
                assert ws.receive_json()["type"] == "pong"
            # 连接关闭后观看者计数回落（驱动 Hub 降频）
            deadline = time.time() + 2
            while time.time() < deadline and hub.viewers > 0:
                time.sleep(0.05)
            assert hub.viewers == 0
        finally:
            hub.stop()

        # 非法 token：握手被拒/立即关闭（Starlette 在连接或首次接收时抛出）
        with pytest.raises(WebSocketDisconnect):
            with tc.websocket_connect("/api/ws/realtime?token=bad-token") as ws:
                ws.receive_json()


def test_realtime_status_endpoint(app_env, monkeypatch):
    """状态端点：Hub 未启用报 enabled=false；启用后带源清单（打码）与计数。"""
    import tempfile

    from api.realtime_hub import RealtimeHub
    from starlette.testclient import TestClient

    from api.deps import create_access_token

    token, _ = create_access_token("u_admin", "admin", "admin")
    with TestClient(app_env) as tc:
        r = tc.get("/api/realtime/status", headers=_auth(token))
        assert r.status_code == 200
        assert r.json()["enabled"] is False

        class StubEngine:
            def analyze(self, frame, source_key="default"):
                return [], {"status": "合规", "level": "safe",
                            "violations": [], "safe": []}

            def draw(self, frame, comp):
                return frame

        hub = RealtimeHub(["demo://"], engine=StubEngine())
        hub.start()
        monkeypatch.setattr("api.routers.ws.get_hub", lambda: hub)
        try:
            r = tc.get("/api/realtime/status", headers=_auth(token))
            body = r.json()
            assert body["enabled"] is True and body["running"] is True
            assert body["sources"][0]["source"].startswith("demo://")
        finally:
            hub.stop()


# ---------- 下载门面单测（防穿越核心逻辑） ----------

def test_load_export_file_rejects_traversal(tmp_path, monkeypatch):
    """下载门面：basename 归一后解析结果必须恒落在 exports 目录内。"""
    import services.export_service as es

    monkeypatch.setattr(es, "data_path", lambda *p: str(tmp_path / "exports"))
    (tmp_path / "exports").mkdir()
    (tmp_path / "exports" / "ok.pdf").write_bytes(b"%PDF-1.4")

    path, name = es.load_export_file("ok.pdf")
    assert os.path.abspath(path) == str(tmp_path / "exports" / "ok.pdf")
    assert name == "ok.pdf"
    # 带 ../ 或子目录的输入统一塌缩到末段文件名：目录部分被剥掉，
    # 解析结果仍在 exports 内 → 命中的只能是 exports/ 下同名文件（不存在则 404）
    with pytest.raises(FileNotFoundError):
        es.load_export_file("../app.db")
    with pytest.raises(FileNotFoundError):
        es.load_export_file("sub/dir.pdf")
    with pytest.raises(ValueError):
        es.load_export_file("..")
    with pytest.raises(FileNotFoundError):
        es.load_export_file("missing.pdf")


# ---------- Phase 3 增补：历史路由 / 媒体下发 / SPA fallback ----------

@pytest.mark.asyncio
async def test_history_endpoints(client):
    token = await _staff_token(client)
    for path in ("/api/history/records", "/api/history/stats-by-date",
                 "/api/history/severity-breakdown", "/api/history/task-risks"):
        r = await client.get(path, headers=_auth(token))
        assert r.status_code == 200, f"{path}: {r.text}"
    # responsible 不可读历史分析
    resp_token = await _login(client, RESP)
    r = await client.get("/api/history/records", headers=_auth(resp_token))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_media_serving_and_guards(app_env, client, monkeypatch, tmp_path):
    """媒体端点：data/ 内图片可取；越界/坏扩展名 400；缺失 404；未登录 401。"""
    import services.media_service as media_service

    fake_data = tmp_path / "data"
    (fake_data / "uploads").mkdir(parents=True)
    (fake_data / "uploads" / "shot.png").write_bytes(_png())
    monkeypatch.setattr(media_service, "BASE_DIR", tmp_path)
    monkeypatch.setattr(media_service, "DATA_DIR", fake_data)

    token = await _staff_token(client)
    ok = await client.get("/api/media/data/uploads/shot.png",
                          headers=_auth(token))
    assert ok.status_code == 200
    assert ok.headers["content-type"].startswith("image/png")

    # <img> 无法带 header：查询参数 token 亦可认证
    from urllib.parse import quote as _q
    qtok = await client.get(f"/api/media/data/uploads/shot.png?token={_q(token)}")
    assert qtok.status_code == 200

    assert (await client.get("/api/media/data/uploads/missing.png",
                             headers=_auth(token))).status_code == 404
    bad = await client.get("/api/media/data/uploads/../app.db",
                           headers=_auth(token))
    assert bad.status_code in (400, 404)
    bad_ext = await client.get("/api/media/data/uploads/x.exe",
                               headers=_auth(token))
    assert bad_ext.status_code == 400
    assert (await client.get(
        "/api/media/data/uploads/shot.png")).status_code == 401


@pytest.mark.asyncio
async def test_spa_fallback_when_dist_present(app_env, monkeypatch, tmp_path):
    """dist 存在时：未知 GET 路径回 index.html（深链路），真实文件直出。"""
    import api.main as api_main

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>zhg-spa</html>", encoding="utf-8")
    (dist / "favicon.ico").write_bytes(b"ico")
    (dist / "assets" / "index-abc123.js").write_text("console.log(1)",
                                                     encoding="utf-8")
    monkeypatch.setattr(api_main, "_DIST", dist)

    app = api_main.create_app()
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://testserver") as c:
        assert (await c.get("/healthz")).status_code == 200  # API 不受影响
        deep = await c.get("/orders/123")                    # 深链路
        assert deep.status_code == 200
        assert "zhg-spa" in deep.text
        fav = await c.get("/favicon.ico")                    # 真实文件直出
        assert fav.status_code == 200
        assert fav.content == b"ico"
        asset = await c.get("/assets/index-abc123.js")       # assets 挂载
        assert asset.status_code == 200
        # /api 未匹配路径保持 404 语义（不被 SPA 兜底吞掉）
        api_miss = await c.get("/api/nonexistent")
        assert api_miss.status_code == 404
        assert "zhg-spa" not in api_miss.text


@pytest.mark.asyncio
async def test_capabilities_includes_hazard_options(client):
    token = await _staff_token(client)
    r = await client.get("/api/tasks/capabilities", headers=_auth(token))
    body = r.json()
    assert r.status_code == 200
    keys = [it["key"] for it in body["hazard_options"]]
    assert "no_helmet" in keys and "spark" in keys
    assert all(k != "none" for k in keys)
    # safe 正向信号不作为上报项下发
    assert all(it["severity"] in ("critical", "warning")
               for it in body["hazard_options"])
