"""告警外部推送服务：Webhook / 企业微信 / 钉钉（零第三方依赖，仅 urllib）。

- 配置读取 notify.*（config/config.yaml），默认关闭；
- 推送结果写 notification_logs 留痕（sent / failed / skipped）；
- push_alarm_async 用 daemon 线程执行，不阻塞实时监测主链路；
- test_push 供管理端按钮验证通道连通性；
- 演示模式（demo_mode）：不发真实 HTTP，捕获 payload 到 data/mock_capture.jsonl，
  notification_logs 标"（模拟）"，无真实 webhook 亦可走通自检闭环；
- db_path/conn 可注入，便于测试使用内存库。
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from core.config import ConfigLoader
from dao.db import DEFAULT_DB_PATH, get_conn, init_db
from dao.models import AlarmEventDAO, NotificationLogDAO

CHANNEL_LABEL = {"wecom": "企业微信", "dingtalk": "钉钉", "generic": "通用 Webhook"}


class NotificationService:
    """构造各渠道 payload 并通过 webhook 推送告警。"""

    def __init__(self, cfg: ConfigLoader | None = None,
                 db_path: str | None = None,
                 conn=None,
                 demo_mode: bool | None = None) -> None:
        self.cfg = cfg or ConfigLoader()
        self.db_path = db_path
        self._conn = conn
        self._demo_mode_override = demo_mode

    def _get_conn(self):
        if self._conn is not None:
            return self._conn
        return get_conn(self.db_path or DEFAULT_DB_PATH)

    # ---------- 配置 ----------
    def _conf(self) -> dict:
        conf = self.cfg.get("notify")
        return conf if isinstance(conf, dict) else {}

    def enabled(self) -> bool:
        return bool(self._conf().get("enabled"))

    def channel(self) -> str:
        return str(self._conf().get("channel", "generic") or "generic")

    def webhook_url(self) -> str:
        return str(self._conf().get("webhook_url", "") or "").strip()

    def retries(self) -> int:
        return max(0, int(self._conf().get("retries", 2) or 0))

    def timeout_sec(self) -> float:
        return max(0.5, float(self._conf().get("timeout_sec", 5) or 5))

    def image_base_url(self) -> str:
        return str(self._conf().get("image_base_url", "") or "").strip().rstrip("/")

    def cooldown_sec(self) -> float:
        return max(0.0, float(self._conf().get("cooldown_sec", 60) or 60))

    def _demo_mode(self) -> bool:
        """演示模式：True 时不发真实 HTTP，仅捕获 payload。显式参数优先于 config。"""
        if self._demo_mode_override is not None:
            return bool(self._demo_mode_override)
        return bool(self._conf().get("demo_mode", False))

    def _log_channel(self) -> str:
        ch = self.channel()
        return f"{ch}（模拟）" if self._demo_mode() else ch

    def _capture(self, payload: dict) -> None:
        """演示模式：把 payload 追加到 data/mock_capture.jsonl，不发真实 HTTP。"""
        try:
            os.makedirs("data", exist_ok=True)
            rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "channel": self.channel(), "payload": payload}
            with open(os.path.join("data", "mock_capture.jsonl"),
                      "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:  # noqa: BLE001 捕获失败不应中断推送主链路
            pass

    def _image_url(self, image_path: str | None) -> str:
        if not image_path:
            return ""
        base = self.image_base_url()
        rel = image_path.replace("\\", "/").lstrip("/")
        return f"{base}/{rel}" if base else rel

    # ---------- 文案 ----------
    @staticmethod
    def _cls_cn(cls: str | None) -> str:
        try:
            from core.compliance import _label
            return _label(cls or "")
        except Exception:  # noqa: BLE001
            return cls or ""

    def _markdown(self, alarm: dict) -> str:
        img = self._image_url(alarm.get("image_path"))
        lines = [
            "### \u26a0\ufe0f 安全告警（海之子·动火安全智能体）",
            f"- 告警 ID：`{alarm.get('id', '')}`",
            f"- 隐患类别：{self._cls_cn(alarm.get('cls'))}（{alarm.get('cls', '')}）",
            f"- 置信度：{float(alarm.get('conf') or 0):.2f}",
            f"- 场景：{alarm.get('scene_id') or '—'}",
            f"- 来源：{alarm.get('source') or 'camera'}",
            f"- 时间：{alarm.get('created_at') or ''}",
        ]
        if img:
            lines.append(f"- 现场证据：[查看截图]({img})")
        return "\n".join(lines)

    def build_payload(self, alarm: dict) -> dict | None:
        """按渠道构造机器人/通用 payload。"""
        if not isinstance(alarm, dict) or not alarm.get("id"):
            return None
        channel = self.channel()
        if channel == "wecom":
            return {
                "msgtype": "markdown",
                "markdown": {"content": self._markdown(alarm)},
            }
        if channel == "dingtalk":
            return {
                "msgtype": "markdown",
                "markdown": {
                    "title": f"安全告警 {alarm.get('cls', '')}",
                    "text": self._markdown(alarm),
                },
            }
        return {
            "title": "安全告警",
            "content": self._markdown(alarm),
            "alarm_id": alarm.get("id"),
            "cls": alarm.get("cls"),
            "conf": alarm.get("conf"),
            "scene_id": alarm.get("scene_id"),
            "source": alarm.get("source"),
            "time": alarm.get("created_at"),
        }

    # ---------- 推送 ----------
    @staticmethod
    def _alarm_row_to_dict(row) -> dict:
        return dict(row) if row is not None else {}

    def push_alarm(self, alarm_id: str) -> dict:
        """同步推送一次告警并写 notification_logs；返回结果字典。"""
        conn = self._get_conn()
        init_db(conn)
        logs = NotificationLogDAO(conn)
        log_channel = self._log_channel()
        demo = self._demo_mode()

        if not demo and (not self.enabled() or not self.webhook_url()):
            logs.insert(alarm_id, log_channel, "skipped",
                        "推送未启用或未配置 webhook_url")
            return {"ok": False, "status": "skipped",
                    "error": "推送未启用或未配置 webhook_url"}

        alarm = self._alarm_row_to_dict(AlarmEventDAO(conn).get_by_id(alarm_id))
        if not alarm:
            logs.insert(alarm_id, log_channel, "error", "告警不存在")
            return {"ok": False, "status": "error", "error": "告警不存在"}

        payload = self.build_payload(alarm)
        if payload is None:
            logs.insert(alarm_id, log_channel, "error", "payload 构造失败")
            return {"ok": False, "status": "error", "error": "payload 构造失败"}

        last_error = "未知错误"
        for attempt in range(self.retries() + 1):
            try:
                self._post(payload)
                logs.insert(alarm_id, log_channel, "sent", None)
                return {"ok": True, "status": "sent"}
            except urllib.error.HTTPError as exc:
                last_error = f"HTTP {exc.code}"
                try:
                    body = exc.read(200).decode("utf-8", "ignore")
                    data = json.loads(body)
                    if data.get("errcode") == 0:
                        logs.insert(alarm_id, log_channel, "sent", None)
                        return {"ok": True, "status": "sent"}
                    last_error = f"errcode={data.get('errcode')} " \
                                 f"{data.get('errmsg', '')}"
                except Exception:  # noqa: BLE001 响应体不可解析则按 HTTP 错误处理
                    last_error = f"HTTP {exc.code}"
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)[:200]
            if attempt < self.retries():
                time.sleep(0.5 * (attempt + 1))

        logs.insert(alarm_id, log_channel, "failed", last_error)
        return {"ok": False, "status": "failed", "error": last_error}

    def push_alarm_async(self, alarm_id: str) -> None:
        """异步推送（daemon 线程），调用方立即返回。"""
        threading.Thread(target=self.push_alarm, args=(alarm_id,),
                         daemon=True).start()

    def test_push(self) -> dict:
        """管理端测试推送：构造一条示例告警并发到当前通道。"""
        sample = {
            "id": "al_test",
            "cls": "spark",
            "conf": 0.92,
            "scene_id": "hot_work",
            "source": "测试",
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "image_path": None,
        }
        conn = self._get_conn()
        init_db(conn)
        logs = NotificationLogDAO(conn)
        log_channel = self._log_channel()
        demo = self._demo_mode()
        if not demo and (not self.enabled() or not self.webhook_url()):
            logs.insert("al_test", log_channel, "skipped",
                        "推送未启用或未配置 webhook_url")
            return {"ok": False, "status": "skipped",
                    "error": "推送未启用或未配置 webhook_url"}
        try:
            self._post(self.build_payload(sample))
            logs.insert("al_test", log_channel, "sent", None)
            return {"ok": True, "status": "sent"}
        except Exception as exc:  # noqa: BLE001
            err = str(exc)[:200]
            logs.insert("al_test", log_channel, "failed", err)
            return {"ok": False, "status": "failed", "error": err}

    def _post(self, payload: dict) -> None:
        if self._demo_mode():
            self._capture(payload)
            return
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.webhook_url(), data=data,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout_sec()) as resp:
            if resp.status >= 400:
                raise urllib.error.HTTPError(
                    self.webhook_url(), resp.status, "push failed",
                    resp.headers, resp)