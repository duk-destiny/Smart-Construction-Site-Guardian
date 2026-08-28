"""Phase 0/1 回归测试：上传魔数/大小校验、webhook SSRF 防护、路径锚点、
get_conn 缺省路径运行时读取（测试可替换 DEFAULT_DB_PATH）。
"""
from __future__ import annotations

import io

import pytest

from core.upload_guard import check_upload, sniff_kind
from services.notify_service import NotificationService


# ---------- 上传魔数/大小 ----------

def test_sniff_kind_magic():
    assert sniff_kind(b"\xff\xd8\xff\xe0\x00\x10JFIF") == "jpg"
    assert sniff_kind(b"\x89PNG\r\n\x1a\n000") == "png"
    assert sniff_kind(b"%PDF-1.7 xxx") == "pdf"
    assert sniff_kind(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 8) == "mp4"
    assert sniff_kind(b"not a known file") is None


def test_check_upload_ok_and_mismatch():
    ok, err = check_upload(b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"x" * 10, "a.jpg")
    assert ok and not err
    ok, err = check_upload(b"\x89PNG\r\n\x1a\n" + b"x" * 10, "a.jpg")
    assert not ok and "不一致" in err          # 扩展名与魔数不符
    ok, err = check_upload(b"x" * 10, "a.exe")
    assert not ok and "不支持的扩展名" in err


def test_check_upload_size_limit():
    # max_image_mb 收紧到 0（任何非空图片超限）
    ok, err = check_upload(b"\xff\xd8\xff\xe0\x00\x10JFIF",
                           "a.jpg", limits={"max_image_mb": 0})
    assert not ok and "上限" in err


def test_check_upload_empty():
    ok, err = check_upload(b"", "a.jpg")
    assert not ok and "为空" in err


# ---------- webhook SSRF 防护 ----------

def _svc(notify: dict, demo_mode: bool | None = None):
    from core.config import ConfigLoader
    class _Cfg:
        def get(self, key, default=None):
            if key == "notify":
                return notify
            return default
    return NotificationService(cfg=_Cfg(), demo_mode=demo_mode)


def test_webhook_rejects_http_in_prod():
    svc = _svc({"webhook_url": "http://hook.example.com"})
    assert svc.check_webhook_url() is not None      # 生产仅 https


def test_webhook_rejects_private_ip():
    svc = _svc({"webhook_url": "https://192.168.1.10/hook"})
    assert "内网" in (svc.check_webhook_url() or "")
    svc2 = _svc({"webhook_url": "https://example.com/hook"})
    assert svc2.check_webhook_url() is None


def test_webhook_allows_demo_loopback():
    svc = _svc({"webhook_url": "http://localhost:8000/hook"}, demo_mode=True)
    assert svc.check_webhook_url() is None


def test_webhook_allow_private_flag():
    svc = _svc({"webhook_url": "https://10.0.0.5/hook",
                "allow_private_webhook": True})
    assert svc.check_webhook_url() is None


def test_sanitize_error_masks_query():
    err = "HTTP Error 403: https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=SECRET"
    out = NotificationService._sanitize_error(err)
    assert "key=SECRET" not in out and "?***" in out


# ---------- 路径锚点 ----------

def test_paths_resolve_and_torel():
    from core.paths import BASE_DIR, resolve, to_rel
    assert resolve("data/alarms/x.jpg").endswith("data" + __import__("os").sep + "alarms" + __import__("os").sep + "x.jpg")
    assert to_rel(str(BASE_DIR / "data" / "a.jpg")) == "data/a.jpg"


# ---------- get_conn 缺省路径运行时读取 ----------

def test_get_conn_runtime_default(monkeypatch, tmp_path):
    import dao.db as db
    monkeypatch.setattr(db, "DEFAULT_DB_PATH", str(tmp_path / "x.db"))
    conn = db.get_conn()          # 无参：应落到 monkeypatch 后的默认路径
    try:
        row = conn.execute("PRAGMA database_list").fetchall()
        path = [r for r in row if r["name"] == "main"][0]["file"]
        assert path == str(tmp_path / "x.db")
    finally:
        conn.close()
