"""本地 mock webhook 接收器（模拟企业微信/钉钉/通用 webhook）。

- 监听 127.0.0.1:8099，POST /webhook 返回 200 {"errcode":0}；
- 每次收到的请求体追加写入 data/mock_webhook.log.jsonl，便于断言；
- 启动后输出 MOCK_WEBHOOK_OK，停止按 Ctrl+C。
零第三方依赖（仅 stdlib http.server）。
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "data", "mock_webhook.log.jsonl")
HOST, PORT = "127.0.0.1", 8099


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: dict):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            payload = {"_raw": raw.decode("utf-8", "ignore")}
        record = {
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "path": self.path,
            "payload": payload,
        }
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            f.flush()
        self._send(200, {"errcode": 0, "errmsg": "ok"})

    def do_GET(self):
        self._send(200, {"errcode": 0, "errmsg": "mock alive", "ts": datetime.now().isoformat()})

    def log_message(self, fmt, *args):  # 静默默认日志
        pass


def main() -> int:
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"MOCK_WEBHOOK_OK listening http://{HOST}:{PORT}/webhook log={LOG}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())