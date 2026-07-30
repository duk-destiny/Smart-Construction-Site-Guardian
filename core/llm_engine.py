"""本地 LLM 引擎（可选增强）：本机 Ollama qwen3:8b，缺失自动降级（C1/C13）。

仅与 localhost 通信；不可用或超时返回 None，主流程零影响。
禁止任何外网调用（代码规范 §9）。
"""
from __future__ import annotations

import json
import urllib.request
import urllib.error

from core.config import ConfigLoader


class LlmEngine:
    """本地 Ollama 文案润色引擎。"""

    def __init__(self, base_url: str | None = None, model: str | None = None,
                 timeout: float = 20.0):
        cfg = ConfigLoader().load()
        self.base_url = (base_url or cfg["llm"]["base_url"]).rstrip("/")
        self.model = model or cfg["llm"]["model"]
        self.timeout = timeout
        self._enabled = cfg["llm"].get("enabled", True)

    def available(self) -> bool:
        """健康检查：Ollama 可达且模型存在则返回 True。"""
        if not self._enabled:
            return False
        try:
            req = urllib.request.Request(f"{self.base_url}/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            models = [m.get("name", "") if isinstance(m, dict) else str(m)
                      for m in data.get("models", [])]
            return any(self.model in name for name in models)
        except Exception:
            return False

    def polish(self, prompt: str) -> str | None:
        """生成润色文案；不可用/超时/异常均返回 None（触发模板降级）。"""
        if not self._enabled:
            return None
        try:
            payload = json.dumps({
                "model": self.model,
                "prompt": prompt,
                "stream": False,
            }).encode("utf-8")
            req = urllib.request.Request(
                f"{self.base_url}/api/generate", data=payload,
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data.get("response", "").strip() or None
        except Exception:
            return None
