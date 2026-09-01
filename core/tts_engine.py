"""语音合成客户端（v2.2 对话窗口 · 可选增强 · 静默策略）。

与 `core/asr_engine.py` 同构的 OpenAI 兼容 `/audio/speech` 最小客户端：
未配置 `config.yaml` 的 `tts.*` 时 `available()` 恒为 False，前端据此
弹「模型暂未拥有语音合成能力」提示（能力检测式降级，非静默渲染）；
已配置但调用失败返回 None 并保留 last_error。本地 TTS 明确不做。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from core.config import ConfigLoader


class TtsEngine:
    """OpenAI 兼容 TTS 的最小客户端。"""

    def __init__(self, api_base: str | None = None, api_key: str | None = None,
                 model: str | None = None, voice: str | None = None,
                 timeout: float = 30.0) -> None:
        try:
            cfg = ConfigLoader().get("tts") or {}
        except Exception:  # noqa: BLE001 配置缺失视为未启用
            cfg = {}
        self.api_base = (api_base or cfg.get("api_base") or "").rstrip("/")
        self.api_key = api_key or cfg.get("api_key") or ""
        self.model = model or cfg.get("model") or "tts-1"
        self.voice = voice or cfg.get("voice") or "alloy"
        self.enabled = bool(cfg.get("enabled"))
        self.timeout = timeout
        self.last_error: str | None = None

    def available(self) -> bool:
        """未配置即 False：前端据此弹能力提示而非渲染朗读入口。"""
        return bool(self.enabled and self.api_base and self.api_key)

    def synthesize(self, text: str) -> bytes | None:
        """合成 mp3 音频字节；任何失败返回 None 并记录 last_error。"""
        self.last_error = None
        text = (text or "").strip()
        if not text:
            self.last_error = "空文本"
            return None
        if not self.available():
            self.last_error = "TTS 未配置"
            return None
        if len(text) > 1000:
            text = text[:1000]        # 单次合成上限（对话回复场景足够）
        try:
            body = json.dumps({
                "model": self.model, "input": text, "voice": self.voice,
                "response_format": "mp3"}).encode("utf-8")
            req = urllib.request.Request(
                f"{self.api_base}/audio/speech", data=body,
                headers={"Authorization": f"Bearer {self.api_key}",
                         "Content-Type": "application/json"},
                method="POST")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.read() or None
        except urllib.error.HTTPError as exc:
            self.last_error = f"HTTP {exc.code}: {exc.reason}"
            return None
        except Exception as exc:  # noqa: BLE001 网络/超时等失败降级
            self.last_error = f"{type(exc).__name__}: {exc}"
            return None
