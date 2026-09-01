"""统一 LLM 入口 ChatClient（v2.1 §5.1，M1）：收敛全系统 4 处旁路为一个入口。

四处旁路（action_agent.polish / review_agent.assist_async /
enhance_service 预填 / intent_router._ask_llm）一律经本模块调用，
不再各自直连云端或实例化 LlmEngine。

降级链（云端优先）：
  1 云端 API   —— openai SDK 接 OpenAI 兼容端点（比赛方提供），命中为正常态 `success`；
  2 本地 Ollama —— 复用 core/llm_engine.py::LlmEngine.chat，命中记 `degraded`；
  3 规则模板/人工 —— 由**调用方**持业务模板兜底（ChatClient 不内置业务模板），
                  全链失败返回 status=failed。

预算口径（§7）：`total_deadline_sec` 为全链墙钟总预算（monotonic），
每档超时取 `min(provider_timeout, 剩余预算)`；预算耗尽立即返回 failed。

断路器（进程内时间戳）：云端连续失败 ≥3 次后 30s 内链式调用直接跳本地档，
只加速降级、不影响恢复探测（窗口过后照常试探云端；显式 provider= 指定档不受断路器影响）。

配置宁缺毋错（仿 enhance_service._normalize）：云端条目必须
api_base + api_key + model 齐全，缺任一即整条丢弃。provider 配置单一
来源为 `llm.providers`；enhance 旧键已自配置文件删除（v2.1 §10.4），
本模块的历史回退读取仅兼容未迁移的旧配置文件，下一版本移除。

铁律：LLM 永不进入风险定级路径；本模块只提供通道与降级语义。
"""
from __future__ import annotations

import json
import threading
import time
from typing import Literal

from pydantic import BaseModel

from core.config import ConfigLoader
from core.logging import get_logger

log = get_logger(__name__)

# 断路器参数：云端连续失败阈值与跳档窗口（只加速降级，不影响恢复探测）
_CLOUD_FAIL_LIMIT = 3
_CLOUD_BREAKER_SEC = 30.0

# json_schema 轻量校验支持的类型映射
_JSON_TYPES: dict[str, tuple] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "object": (dict,),
    "array": (list,),
}


class ChatResult(BaseModel):
    """统一 LLM 调用结果（数据契约，逐字稳定）。"""

    content: str | dict | None = None      # 文本，或已解析并通过 schema 校验的 JSON
    provider: str = "none"                 # 实际命中的 provider 名（全败为最后尝试档/none）
    status: Literal["success", "degraded", "failed"] = "failed"
    cost_ms: int = 0
    error: str | None = None


def _validate_schema(obj, schema) -> bool:
    """轻量 JSON Schema 校验：支持 object/required/properties/enum/基础类型。

    未声明的键一律放行（白名单语义由各调用方自持）；schema 非 dict 视为通过。
    """
    if not isinstance(schema, dict):
        return True
    expected = schema.get("type")
    if expected == "object":
        if not isinstance(obj, dict):
            return False
        for key in schema.get("required") or []:
            if key not in obj:
                return False
        for key, sub in (schema.get("properties") or {}).items():
            if key in obj and not _validate_schema(obj[key], sub):
                return False
        return True
    if expected == "null":
        return obj is None
    if expected in _JSON_TYPES:
        if obj is None:
            return False
        if expected == "integer" and isinstance(obj, bool):
            return False
        return isinstance(obj, _JSON_TYPES[expected])
    if "enum" in schema:
        return obj in schema["enum"]
    return True


def _parse_and_validate(raw: str, json_schema: dict | None):
    """从模型输出提取 JSON 并二次校验；失败返回 None（不猜测补全）。"""
    s, e = raw.find("{"), raw.rfind("}")
    if s == -1 or e <= s:
        return None
    try:
        obj = json.loads(raw[s:e + 1])
    except Exception:  # noqa: BLE001 截断/非法 JSON 一律判失败
        return None
    if not isinstance(obj, dict):
        return None
    if json_schema is not None and not _validate_schema(obj, json_schema):
        return None
    return obj


def _normalize_entries(raw: list) -> list[dict]:
    """provider 条目规范化（与 enhance_service._normalize 同语义，宁缺毋错）。

    cloud 条目必须 api_base+api_key+model 齐全，缺任一即整条丢弃；
    local 条目 model 可空（走 LlmEngine 默认模型）。
    """
    out: list[dict] = []
    for i, p in enumerate(raw):
        if not isinstance(p, dict):
            continue
        name = str(p.get("name") or f"p{i + 1}")
        ptype = str(p.get("type")
                    or ("local" if name.lower() == "local" else "cloud")
                    ).strip().lower()
        if ptype not in ("cloud", "local"):
            continue
        entry = {
            "name": name,
            "type": ptype,
            "api_base": str(p.get("api_base") or "").rstrip("/"),
            "api_key": str(p.get("api_key") or ""),
            "model": str(p.get("model") or ""),
            "timeout_sec": float(p.get("timeout_sec") or 20),
        }
        if ptype == "cloud" and not (
                entry["api_base"] and entry["api_key"] and entry["model"]):
            continue
        out.append(entry)
    return out


def _legacy_chain(enh_cfg: dict) -> list[dict]:
    """历史回退（保留一版）：由 enhance 旧键合成等价链，仅兼容未迁移的旧配置。"""
    out: list[dict] = []
    mode = str(enh_cfg.get("provider") or "auto").strip().lower()
    cloud = enh_cfg.get("cloud") or {}
    cbase = str(cloud.get("api_base") or "").rstrip("/")
    ckey = str(cloud.get("api_key") or "")
    if mode in ("auto", "cloud") and cbase and ckey:
        out.append({
            "name": "cloud", "type": "cloud", "api_base": cbase,
            "api_key": ckey,
            "model": str(cloud.get("model") or "gpt-4o-mini"),
            "timeout_sec": float(cloud.get("timeout_sec") or 20),
        })
    if mode in ("auto", "local"):
        out.append({"name": "local", "type": "local", "api_base": "",
                    "api_key": "", "model": "", "timeout_sec": 20.0})
    return out


class ChatClient:
    """全系统统一 LLM 入口。降级链：云端 API → 本地 Ollama →（调用方）规则模板/人工。

    云端是正常态（success）；命中本地及以下任一档均记 degraded；
    全链失败记 failed，业务模板兜底由调用方自持。
    """

    def __init__(self, providers: list[dict] | None = None) -> None:
        self.providers = (providers if providers is not None
                          else self._load_providers())
        self._lock = threading.Lock()
        self._cloud_fail_streak = 0          # 云端连续失败计数
        self._cloud_open_until = 0.0         # 断路器开启截止时间（monotonic）

    # ---------- 配置加载（宁缺毋错）----------
    @staticmethod
    def _load_providers() -> list[dict]:
        """先读 `llm.providers`（单一来源）；仅当其缺省/无有效条目时才尝试历史 enhance 旧键。"""
        try:
            llm_cfg = ConfigLoader().get("llm") or {}
        except Exception:  # noqa: BLE001 配置缺失 = 无 provider
            llm_cfg = {}
        raw = llm_cfg.get("providers")
        if isinstance(raw, list) and raw:
            chain = _normalize_entries(raw)
            if chain:
                return chain
        try:
            enh = ConfigLoader().get("enhance") or {}
        except Exception:  # noqa: BLE001
            enh = {}
        raw2 = enh.get("providers")
        if isinstance(raw2, list) and raw2:
            chain = _normalize_entries(raw2)
            if chain:
                return chain
        return _legacy_chain(enh)

    # ---------- 可用性探活（调用方预检用，如复核辅助的 skipped 态）----------
    def available_provider(self) -> str | None:
        """返回链中将使用的首个 provider 名；云端视配置即用，本地需探活。"""
        for p in self.providers:
            if p["type"] == "cloud":
                return p["name"]
            try:
                from core.llm_engine import LlmEngine
                if LlmEngine(model=p["model"] or None).available():
                    return p["name"]
            except Exception:  # noqa: BLE001 探活失败试下一档
                continue
        return None

    # ---------- 断路器（进程内时间戳，只加速降级）----------
    def _breaker_open(self) -> bool:
        with self._lock:
            return time.monotonic() < self._cloud_open_until

    def _note_cloud_failure(self) -> None:
        with self._lock:
            self._cloud_fail_streak += 1
            if self._cloud_fail_streak >= _CLOUD_FAIL_LIMIT:
                self._cloud_open_until = time.monotonic() + _CLOUD_BREAKER_SEC

    def _note_cloud_success(self) -> None:
        with self._lock:
            self._cloud_fail_streak = 0

    # ---------- 云端档（openai SDK，max_retries=0，尽量 JSON mode）----------
    def _make_client(self, p: dict, timeout: float):
        from openai import OpenAI
        return OpenAI(base_url=p["api_base"], api_key=p["api_key"],
                      timeout=max(timeout, 1.0), max_retries=0)

    def _call_cloud(self, p: dict, system: str, user: str, *,
                    json_schema: dict | None, max_tokens: int,
                    timeout: float):
        client = self._make_client(p, timeout)
        kwargs: dict = {
            "model": p["model"],
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "max_tokens": int(max_tokens),
            "temperature": 0.2,
        }
        if json_schema is not None:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            resp = client.chat.completions.create(**kwargs)
        except Exception as first_exc:
            if json_schema is None:
                raise
            from openai import BadRequestError
            if not isinstance(first_exc, BadRequestError):
                raise  # 网络/超时类错误直接计失败，不做无谓二次尝试
            # 端点不支持 JSON mode → 退化为 prompt 约束（schema 已在提示词中）
            kwargs.pop("response_format", None)
            resp = client.chat.completions.create(**kwargs)
        raw = ((resp.choices[0].message.content or "").strip())
        if not raw:
            # 思考型模型兜底（v2.2）：推理吃光 max_tokens 时 content 为空、
            # reasoning_content 非空、finish_reason=length——加倍预算重试一次
            msg = resp.choices[0].message
            finish = getattr(resp.choices[0], "finish_reason", "") or ""
            reasoning = (getattr(msg, "reasoning_content", None) or "").strip()
            if reasoning and finish == "length":
                kwargs["max_tokens"] = max(int(max_tokens) * 4, 2048)
                resp = client.chat.completions.create(**kwargs)
                raw = ((resp.choices[0].message.content or "").strip())
        if not raw:
            return None
        if json_schema is None:
            return raw
        return _parse_and_validate(raw, json_schema)

    # ---------- 本地档（复用 LlmEngine：think=false/keep_alive/warmup 既有能力）----------
    def _call_local(self, p: dict, system: str, user: str, *,
                    json_schema: dict | None, max_tokens: int,
                    remaining: float):
        from core.llm_engine import LlmEngine
        eng = LlmEngine(model=p["model"] or None,
                        timeout=max(min(20.0, remaining), 1.0))
        if json_schema is not None and hasattr(eng, "ask_json"):
            out = eng.ask_json(f"{system}\n{user}")
            if not isinstance(out, dict) or not _validate_schema(out, json_schema):
                return None
            return out
        out = eng.chat(system, user, num_predict=int(max_tokens))
        if not out:
            return None
        if json_schema is not None:
            return _parse_and_validate(out, json_schema)
        return out

    # ---------- 统一入口 ----------
    def chat(self, system: str, user: str, *,
             json_schema: dict | None = None,
             max_tokens: int = 1024,
             total_deadline_sec: float = 30.0,
             provider: str | None = None) -> ChatResult:
        """全链调用：云端 → 本地逐级降级；`provider=` 显式指定档（不降级）。

        `total_deadline_sec` 为全链墙钟总预算，降级换档耗时全部计入；
        预算耗尽立即返回 failed，由调用方模板兜底。
        """
        t0 = time.monotonic()

        def _fail(err: str, prov: str = "none") -> ChatResult:
            return ChatResult(content=None, provider=prov, status="failed",
                              cost_ms=int((time.monotonic() - t0) * 1000),
                              error=err)

        pinned = provider is not None
        if pinned:
            p = next((x for x in self.providers if x["name"] == provider), None)
            if p is None:
                return _fail(f"未知 provider: {provider}")
            chain: list[dict] = [p]
        else:
            chain = list(self.providers)
        if not chain:
            return _fail("未配置任何 LLM provider")

        last_error: str | None = None
        last_name = "none"
        for p in chain:
            remaining = total_deadline_sec - (time.monotonic() - t0)
            if remaining <= 0:
                return _fail(
                    f"全链总预算耗尽（>{float(total_deadline_sec):.0f}s）",
                    last_name)
            last_name = p["name"]
            # 断路器只作用于链式调用；显式指定档不受影响
            if p["type"] == "cloud" and not pinned and self._breaker_open():
                last_error = f"[{p['name']}] 断路器开启，跳过云端档"
                continue
            timeout = min(p["timeout_sec"], remaining)
            try:
                if p["type"] == "cloud":
                    content = self._call_cloud(
                        p, system, user, json_schema=json_schema,
                        max_tokens=max_tokens, timeout=timeout)
                else:
                    content = self._call_local(
                        p, system, user, json_schema=json_schema,
                        max_tokens=max_tokens, remaining=remaining)
            except Exception as exc:  # noqa: BLE001 单档失败 → 降级下一档
                last_error = f"[{p['name']}] {type(exc).__name__}: {exc}"
                if p["type"] == "cloud":
                    self._note_cloud_failure()
                log.warning(f"ChatClient [{p['name']}] 调用失败: {last_error}")
                continue
            if content is None:
                last_error = (last_error
                              or f"[{p['name']}] 空输出或输出校验失败")
                continue
            if p["type"] == "cloud":
                self._note_cloud_success()
            return ChatResult(
                content=content, provider=p["name"],
                status="success" if p["type"] == "cloud" else "degraded",
                cost_ms=int((time.monotonic() - t0) * 1000), error=None)
        return _fail(last_error or "全部 provider 均失败", last_name)


# ---------- 模块级单例 ----------
_CLIENT: ChatClient | None = None
_CLIENT_LOCK = threading.Lock()


def get_chat_client() -> ChatClient:
    """模块级单例（进程内共享降级状态与断路器时间戳）。"""
    global _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is None:
            _CLIENT = ChatClient()
        return _CLIENT


def reset_chat_client() -> None:
    """清空单例（测试隔离用）。"""
    global _CLIENT
    with _CLIENT_LOCK:
        _CLIENT = None
