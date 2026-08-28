"""语音转写客户端（v0.4，可选增强 · 纯转写调用）。

仅封装 OpenAI 兼容 `/audio/transcriptions` 一次调用，转写结果回填到
统一上报的文本框，后续流程与手打完全一致（P2 文字链路复用，零新管线）。

静默策略（用户约定）：未配置 `config.yaml` 的 `asr.*` 时 `available()` 恒为
False，UI 层根本不渲染语音入口——不提示、不灰显、无任何噪音；
已配置但调用失败（断网/密钥失效）则返回 None 并保留 last_error，
由调用方决定是否轻提示。本地 whisper 与音频文件上传明确不做。
"""
from __future__ import annotations

import io
import json
import time
import uuid
import wave

import urllib.error
import urllib.request

from core.config import ConfigLoader


class AsrEngine:
    """OpenAI 兼容 ASR 的最小客户端。"""

    def __init__(self, api_base: str | None = None, api_key: str | None = None,
                 model: str | None = None, timeout: float = 30.0) -> None:
        try:
            cfg = ConfigLoader().get("asr") or {}
        except Exception:  # noqa: BLE001 配置缺失视为未启用
            cfg = {}
        self.api_base = ((api_base or cfg.get("api_base") or "").rstrip("/"))
        self.api_key = api_key or cfg.get("api_key") or ""
        self.model = model or cfg.get("model") or "whisper-1"
        self.enabled = bool(cfg.get("enabled"))
        self.timeout = timeout
        self.last_error: str | None = None

    def available(self) -> bool:
        """未配置即 False：UI 据此完全不渲染语音入口（静默）。"""
        return bool(self.enabled and self.api_base and self.api_key)

    # ---------- 通道连通性自检（v0.8）----------
    @staticmethod
    def _tiny_wav(seconds: float = 0.5) -> bytes:
        """内存合成 0.5s 8kHz 单声道静音 wav，作连通性检查的极小音频。"""
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(8000)
            wf.writeframes(b"\x00\x00" * int(8000 * seconds))
        return buf.getvalue()

    def check_connectivity(self, timeout: float | None = None) -> dict:
        """云 ASR 通道连通性自检：极小静音 wav 端到端走一次转写。

        验证 端点+key+model 三件套（区别于仅探活的 /models GET）；
        返回 {ok, status(ok/unconfigured/error), detail, cost_ms}，
        供系统自检页与演示前一键确认 key 有效。
        """
        result: dict = {"ok": False, "status": "error",
                        "detail": "", "cost_ms": 0}
        if not self.available():
            result["status"] = "unconfigured"
            result["detail"] = "未配置（asr.enabled/api_base/api_key）"
            return result
        try:
            body, ctype = self.build_multipart(
                self._tiny_wav(), "check.wav",
                {"model": self.model, "language": "zh"})
            req = urllib.request.Request(
                f"{self.api_base}/audio/transcriptions", data=body,
                headers={"Authorization": f"Bearer {self.api_key}",
                         "Content-Type": ctype},
                method="POST")
            t0 = time.monotonic()
            with urllib.request.urlopen(
                    req, timeout=timeout or min(self.timeout, 15.0)) as resp:
                resp.read()
            result.update(
                ok=True, status="ok", cost_ms=int((time.monotonic() - t0) * 1000),
                detail=f"{self.model} 转写端到端可达")
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                result["detail"] = f"key 无效或无权限（HTTP {exc.code}）"
            elif exc.code == 404:
                result["detail"] = ("端点路径不对（HTTP 404，"
                                    "确认 api_base 含 /v1）")
            elif exc.code == 400:
                result["detail"] = ("请求被拒（HTTP 400）：该服务对静音/极短"
                                    "音频有限制，key 鉴权大概率有效，建议"
                                    "用真实录音再验一次")
            else:
                result["detail"] = f"服务端拒绝（HTTP {exc.code}）"
        except Exception as exc:  # noqa: BLE001 网络/超时等可读呈现
            result["detail"] = f"不可达：{type(exc).__name__}: {exc}"[:120]
        return result

    @staticmethod
    def build_multipart(data: bytes, filename: str,
                        fields: dict[str, str]) -> tuple[bytes, str]:
        """构造 multipart/form-data 请求体（纯函数，便于单测）。"""
        boundary = "----hz" + uuid.uuid4().hex
        parts: list[bytes] = []
        for name, value in fields.items():
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; '
                f'name="{name}"\r\n\r\n{value}\r\n'.encode())
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; '
            f'name="file"; filename="{filename}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n".encode()
            + data + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        body = b"".join(parts)
        ctype = f"multipart/form-data; boundary={boundary}"
        return body, ctype

    def transcribe(self, data: bytes, filename: str = "record.wav",
                   language: str = "zh") -> str | None:
        """转写音频字节；任何失败返回 None 并记录 last_error（不断链路）。"""
        self.last_error = None
        if not data:
            self.last_error = "空音频"
            return None
        if not self.available():
            self.last_error = "ASR 未配置"
            return None
        try:
            body, ctype = self.build_multipart(
                data, filename,
                {"model": self.model, "language": language})
            req = urllib.request.Request(
                f"{self.api_base}/audio/transcriptions", data=body,
                headers={"Authorization": f"Bearer {self.api_key}",
                         "Content-Type": ctype},
                method="POST")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            text = (payload.get("text") or "").strip()
            return text or None
        except Exception as exc:  # noqa: BLE001 失败降级返回 None
            self.last_error = f"{type(exc).__name__}: {exc}"
            return None
