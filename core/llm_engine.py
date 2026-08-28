"""本地 LLM 引擎（可选增强）：本机 Ollama，缺失/超时自动降级（C1/C13）。

仅与 localhost 的 Ollama 守护进程通信（独立进程，规避 torch/onnxruntime
同进程原生线程冲突）；不可用或超时返回 None，主流程零影响。
实时链路不调用本引擎；仅处置 Agent 工单落库后异步润色工人提示（LLD §3.5/§5.1）。

调用走 /api/chat（system+user 消息，应用 chat 模板）；qwen3 等思考模型需
显式 think=false，否则思考会吞尽 num_predict 预算导致空输出（润色静默失效）。
每次请求带 keep_alive（默认 30m）让模型常驻内存，app 启动后台 warmup() 一次
把 5.2GB 模型预加载，之后润色全走热调用（冷启 ~11s → 热 ~3s）。
本地 transformers 直跑 Qwen 权重不接入：实测 15s/条且与 onnxruntime 同进程
抢原生线程有崩溃风险，ollama 独立进程路径更稳更快。
"""
from __future__ import annotations

import json
import urllib.request
import urllib.error

from core.config import ConfigLoader
from core.logging import get_logger

log = get_logger(__name__)

# 润色专用系统提示：强约束只依据给定信息、不得编造法规名称/编号（安全系统铁律）
_SYSTEM = (
    "你是工地安全提醒助手。用一线工人听得懂的大白话输出整改提示，语气直接、可执行。"
    "只依据用户给出的隐患说明、规范依据、整改要求、处理时限组织语言，"
    "不得编造未给出的法规名称、条款编号或标准号。"
)


class LlmEngine:
    """本地 Ollama 文案润色引擎（/api/chat + keep_alive 常驻）。"""

    _warmed = False  # 进程级守卫：warmup 只跑一次

    def __init__(self, base_url: str | None = None, model: str | None = None,
                 timeout: float = 20.0, think: bool | None = None,
                 num_predict: int | None = None, temperature: float | None = None,
                 keep_alive: str | None = None):
        cfg = ConfigLoader().load()
        llm_cfg = cfg.get("llm", {})
        self.base_url = (base_url or llm_cfg["base_url"]).rstrip("/")
        self.model = model or llm_cfg["model"]
        self.timeout = timeout
        self._enabled = llm_cfg.get("enabled", True)
        # think=None 随模型默认；False 显式关思考（qwen3 思考会吞 num_predict 致空输出）
        self._think = llm_cfg.get("think", False) if think is None else think
        self._num_predict = int(llm_cfg.get("num_predict", 220) if num_predict is None else num_predict)
        self._temperature = float(llm_cfg.get("temperature", 0.3) if temperature is None else temperature)
        # ollama 模型常驻时长：避免 5.2GB 模型反复冷启；"0"=请求后即卸载
        self._keep_alive = llm_cfg.get("keep_alive", "30m") if keep_alive is None else keep_alive

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
        """生成润色文案；不可用/超时/异常/空输出均返回 None（触发模板降级）。

        走 /api/chat（system+user），qwen3 显式 think=false，keep_alive 常驻防冷启。
        """
        return self.chat(_SYSTEM, prompt, num_predict=self._num_predict)

    def chat(self, system: str, user: str,
             num_predict: int | None = None) -> str | None:
        """通用单轮 /api/chat（Agent 测试场按 base 对比润色用）。

        不可用/超时/异常/空输出一律返回 None；调用方自行降级。
        """
        if not self._enabled:
            return None
        body = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "options": {"num_predict": int(num_predict or self._num_predict),
                        "temperature": self._temperature},
            "keep_alive": self._keep_alive,
        }
        if self._think is not None:
            body["think"] = self._think
        try:
            req = urllib.request.Request(
                f"{self.base_url}/api/chat", data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            msg = (data.get("message") or {}).get("content", "").strip()
            return msg or None
        except Exception:
            return None

    def ask_json(self, instruction: str) -> dict | None:
        """受约束 JSON 分类调用（P3 意图第 2 层兜底专用，非判定路径）。

        与 polish 同管线（/api/chat），系统提示要求仅输出 JSON；
        解析失败/超时/未启用一律返回 None——由上层规则与人工确认兜底。
        """
        if not self._enabled:
            return None
        body = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system",
                 "content": "你是文本意图解析器。只输出一个 JSON 对象，"
                            "字段和取值严格按用户给定的白名单，"
                            "不确定就用 null，禁止输出解释或多余字符。"},
                {"role": "user", "content": instruction},
            ],
            "options": {"num_predict": max(self._num_predict, 96),
                        "temperature": self._temperature},
            "keep_alive": self._keep_alive,
        }
        if self._think is not None:
            body["think"] = False
        try:
            req = urllib.request.Request(
                f"{self.base_url}/api/chat", data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            raw = (data.get("message") or {}).get("content", "").strip()
            start, end = raw.find("{"), raw.rfind("}")
            if start == -1 or end <= start:
                return None
            obj = json.loads(raw[start:end + 1])
            return obj if isinstance(obj, dict) else None
        except Exception as exc:  # noqa: BLE001 分类失败交还规则层，但留痕
            log.warning(f"LLM ask_json 失败（交还规则层）: {type(exc).__name__}: {exc}")
            return None

    def warmup(self) -> None:
        """后台预热：触发 ollama 加载模型并 keep_alive 常驻；进程内只跑一次，失败静默。

        app 启动期由守护线程调用，把 5.2GB 模型预加载进内存，之后润色全走热调用。
        """
        if LlmEngine._warmed or not self._enabled:
            return
        LlmEngine._warmed = True
        body = {
            "model": self.model,
            "stream": False,
            "messages": [{"role": "user", "content": "ping"}],
            "options": {"num_predict": 1, "temperature": 0.1},
            "keep_alive": self._keep_alive,
        }
        if self._think is not None:
            body["think"] = self._think
        try:
            req = urllib.request.Request(
                f"{self.base_url}/api/chat", data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=60) as resp:
                resp.read()
        except Exception:
            pass
