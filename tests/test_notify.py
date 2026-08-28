"""告警外部推送测试：payload 构造、推送成功/失败/禁用留痕、完整告警链路、后台轮询。"""

import io
import json
import os
import urllib.error

import numpy as np

from dao.db import get_conn, init_db
from dao.models import AlarmEventDAO, NotificationLogDAO
from services.notify_service import NotificationService
from services.task_service import TaskService
from services.monitor_service import RtspMonitor


class _FakeCfg:
    """用字典模拟 ConfigLoader.get 的点路径取值。"""

    def __init__(self, data: dict) -> None:
        self._data = data

    def get(self, key: str, default=None):
        node = self._data
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node


class _FakeResp:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeEngine:
    """不加载模型的哑引擎，仅供 RtspMonitor 构造。"""

    def analyze(self, frame):
        return [], {}

    def draw(self, frame, comp):
        return frame


def _svc(cfg_data: dict, conn=None) -> NotificationService:
    return NotificationService(cfg=_FakeCfg(cfg_data), conn=conn)


SAMPLE_ALARM = {
    "id": "al_1",
    "cls": "spark",
    "conf": 0.91,
    "scene_id": "hot_work",
    "source": "rtsp://cam1",
    "created_at": "2026-08-04 10:00:00",
    "image_path": "data/alarms/s1_spark_1.jpg",
}


def test_payload_wecom():
    svc = _svc({"notify": {"channel": "wecom", "enabled": True,
                           "webhook_url": "http://hook"}})
    payload = svc.build_payload(SAMPLE_ALARM)
    assert payload["msgtype"] == "markdown"
    assert "spark" in payload["markdown"]["content"]
    assert "al_1" in payload["markdown"]["content"]


def test_payload_dingtalk():
    svc = _svc({"notify": {"channel": "dingtalk", "enabled": True,
                           "webhook_url": "http://hook"}})
    payload = svc.build_payload(SAMPLE_ALARM)
    assert payload["msgtype"] == "markdown"
    assert "spark" in payload["markdown"]["text"]


def test_payload_generic():
    svc = _svc({"notify": {"channel": "generic", "enabled": True,
                           "webhook_url": "http://hook"}})
    payload = svc.build_payload(SAMPLE_ALARM)
    assert payload["alarm_id"] == "al_1"
    assert payload["cls"] == "spark"
    assert payload["source"] == "rtsp://cam1"


def test_push_disabled_logs_skipped():
    conn = get_conn(":memory:")
    init_db(conn)
    svc = _svc({"notify": {"enabled": False, "channel": "generic",
                           "webhook_url": ""}}, conn=conn)
    res = svc.push_alarm("al_none")
    assert res["status"] == "skipped"
    rows = NotificationLogDAO(conn).list_all()
    assert rows and rows[0]["status"] == "skipped"


def test_push_success_mock_urlopen(monkeypatch):
    conn = get_conn(":memory:")
    init_db(conn)
    aid = AlarmEventDAO(conn).insert("s1", None, "hot_work", "spark", 0.9,
                                     source="camera")
    svc = _svc({"notify": {"enabled": True, "channel": "generic",
                           "webhook_url": "https://example.com/hook"}}, conn=conn)
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeResp()

    monkeypatch.setattr("services.notify_service.urllib.request.urlopen",
                        fake_urlopen)
    res = svc.push_alarm(aid)
    assert res["ok"] is True and res["status"] == "sent"
    assert captured["body"]["alarm_id"] == aid
    rows = NotificationLogDAO(conn).list_by_alarm(aid)
    assert rows and rows[0]["status"] == "sent"


def test_push_failed_errcode(monkeypatch):
    conn = get_conn(":memory:")
    init_db(conn)
    aid = AlarmEventDAO(conn).insert("s1", None, "hot_work", "smoke", 0.8)
    svc = _svc({"notify": {"enabled": True, "channel": "wecom",
                           "webhook_url": "https://example.com/hook", "retries": 0}},
               conn=conn)

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(
            "https://example.com/hook", 400, "bad", {},
            io.BytesIO(b'{"errcode": 93000, "errmsg": "invalid webhook"}'))

    monkeypatch.setattr("services.notify_service.urllib.request.urlopen",
                        fake_urlopen)
    res = svc.push_alarm(aid)
    assert res["status"] == "failed"
    assert "93000" in res["error"]
    rows = NotificationLogDAO(conn).list_by_alarm(aid)
    assert rows and rows[0]["status"] == "failed"


def test_raise_alarm_full_chain(tmp_path, monkeypatch):
    conn = get_conn(":memory:")
    init_db(conn)
    svc = TaskService(conn)
    notified = []
    monkeypatch.setattr(
        "services.notify_service.NotificationService.push_alarm_async",
        lambda self, aid: notified.append(aid))
    monkeypatch.setattr("core.evidence.EVIDENCE_DIR", str(tmp_path))
    frame = np.zeros((64, 64, 3), dtype=np.uint8)

    aid = svc.raise_alarm("s1", "hot_work", "spark", 0.91,
                          source="rtsp://cam1", annotated_bgr=frame)
    assert aid
    row = svc.alarms.get_by_id(aid)
    assert row["source"] == "rtsp://cam1"
    assert row["image_path"] and os.path.exists(row["image_path"])
    assert row["image_path"].endswith(".jpg")
    assert notified == [aid]

    # 同会话同类未关闭告警去重：不再创建
    assert svc.raise_alarm("s1", "hot_work", "spark", 0.9,
                           source="rtsp://cam1", annotated_bgr=frame) is None


def test_monitor_poll_once_cooldown(tmp_path, monkeypatch):
    results = [{
        "index": 0, "source": "rtsp://cam1", "ok": True,
        "detections": [{"scene": "hot_work", "cls": "spark", "conf": 0.9}],
        "compliance": {"level": "critical", "status": "不合规",
                       "reasons": [], "violations": []},
        "annotated": None,
    }]
    monkeypatch.setattr(
        "services.monitor_service.MultiSourceMonitor.grab_all",
        lambda self, analyze, draw: results)
    monkeypatch.setattr(
        "services.notify_service.NotificationService.push_alarm_async",
        lambda self, aid: None)

    mon = RtspMonitor(["rtsp://cam1"], interval_sec=60, cooldown_sec=3600,
                      engine=_FakeEngine(), db_path=":memory:")
    assert mon.poll_once() == 1
    assert mon.alarms == 1
    # 冷却期内同源同类不再重复告警
    assert mon.poll_once() == 0
    assert mon.alarms == 1


def test_demo_test_push_no_http(monkeypatch, tmp_path):
    """演示模式 test_push：不发真实 HTTP，捕获 payload 到 mock_capture.jsonl，返回 sent。"""
    monkeypatch.setattr(
        "services.notify_service.data_path",
        lambda *parts: str(tmp_path.joinpath("data", *parts)))
    conn = get_conn(":memory:")
    init_db(conn)
    svc = NotificationService(
        cfg=_FakeCfg({"notify": {"enabled": True, "channel": "generic",
                                 "webhook_url": ""}}),
        conn=conn, demo_mode=True)

    called = {"http": 0}

    def _no_http(*a, **k):
        called["http"] += 1
        raise AssertionError("演示模式不应发真实 HTTP")

    monkeypatch.setattr("services.notify_service.urllib.request.urlopen", _no_http)

    res = svc.test_push()
    assert res["ok"] is True
    assert res["status"] == "sent"
    assert called["http"] == 0  # 未发任何 HTTP

    cap = tmp_path / "data" / "mock_capture.jsonl"
    assert cap.exists()
    lines = [ln for ln in cap.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["channel"] == "generic"
    assert rec["payload"]["cls"] == "spark"


def test_demo_push_alarm_full_loop(monkeypatch, tmp_path):
    """演示模式 push_alarm：跳过 enabled/webhook 门禁，捕获 payload，DB 留 sent(模拟)。"""
    monkeypatch.setattr(
        "services.notify_service.data_path",
        lambda *parts: str(tmp_path.joinpath("data", *parts)))
    conn = get_conn(":memory:")
    init_db(conn)
    aid = AlarmEventDAO(conn).insert("s1", None, "hot_work", "spark", 0.9,
                                     source="camera")
    # notify.enabled=False 但 demo_mode=True 应跳过门禁
    svc = NotificationService(
        cfg=_FakeCfg({"notify": {"enabled": False, "channel": "wecom",
                                 "webhook_url": ""}}),
        conn=conn, demo_mode=True)

    called = {"http": 0}
    monkeypatch.setattr(
        "services.notify_service.urllib.request.urlopen",
        lambda *a, **k: called.__setitem__("http", called["http"] + 1))

    res = svc.push_alarm(aid)
    assert res["ok"] is True
    assert res["status"] == "sent"
    assert called["http"] == 0

    rows = NotificationLogDAO(conn).list_by_alarm(aid)
    assert rows and rows[0]["status"] == "sent"
    assert "（模拟）" in rows[0]["channel"]

    cap = tmp_path / "data" / "mock_capture.jsonl"
    assert cap.exists()