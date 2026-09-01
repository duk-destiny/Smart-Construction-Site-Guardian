"""AI 提取预填服务（v0.6 二期a → v0.8 多 Provider → v2.1 统一入口）。

Provider 链（v0.8）：链序即降级序，每项 {name, type(cloud|local),
api_base, api_key, model, timeout_sec}；全部失败退手填表单。
v2.1（云端优先）：provider 链已迁入 `llm.providers`（统一 LLM 入口单一配置源），
旧 `enhance.provider/cloud/providers` 键已自配置文件删除；本模块的历史回退双读
（`_load_providers`）仅兼容未迁移的旧配置文件，下一版本移除。
云端调用不再走裸 urllib，改由 `core/chat_client.py::ChatClient` 提供。
本模块保留白名单校验与 `total_deadline_sec` 总预算语义。
`check_provider` 连通性自检仍用最小 HTTP 探活（不在降级链内，§10 明确保留）。

铁律不变：输出**仅作表单预填草稿**，人工确认后才建单；风险定级仍由
compliance.severity 查表完成——LLM 全程不碰判定路径（Q3/Q6）。
每 provider 输出过双白名单（hazard_key/scene_id），越界即弃、试下一家；
`total_deadline_sec` 为全链总预算，防止多家慢超时叠加拖死预填。
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from core.chat_client import get_chat_client
from core.config import ConfigLoader


def _whitelist() -> tuple[set[str], list[str]]:
    """(隐患键白名单, 场景清单)——均以运行时配置为准。"""
    from core.compliance import SEVERITY
    keys = {k for k, v in SEVERITY.items() if k != "none" and v != "safe"}
    scenes = list((ConfigLoader().get("scenes") or {}).keys())
    return keys, scenes


def _load_providers() -> dict:
    """过渡双读（单一语义）：provider 链先读统一的 `llm.providers`，
    无有效条目时回退 `enhance` 旧键；M1 完成后删除回退分支。"""
    try:
        cfg = dict(ConfigLoader().get("enhance") or {})
    except Exception:  # noqa: BLE001 配置缺失=纯手填模式
        cfg = {}
    try:
        raw = (ConfigLoader().get("llm") or {}).get("providers")
    except Exception:  # noqa: BLE001
        raw = None
    if isinstance(raw, list) and raw and EnhanceEngine._normalize(
            {"providers": raw}):
        cfg["providers"] = raw
    return cfg


class EnhanceEngine:
    """多 Provider 预填提取器：{hazard_key, scene_id, description, location}。"""

    def __init__(self, provider: str | None = None) -> None:
        try:
            cfg = _load_providers()
        except Exception:  # noqa: BLE001 配置缺失=纯手填模式
            cfg = {}
        cfg = dict(cfg)
        if provider:  # 显式参数覆盖 legacy provider 键（v0.6 语义保留）
            cfg["provider"] = provider
        self._mode = str(cfg.get("provider") or "auto").strip().lower()
        # 全链总预算（秒）：多家 provider 串行试错时不至于拖死预填按钮
        self.total_deadline_sec = float(cfg.get("total_deadline_sec") or 30)
        self.providers = self._normalize(cfg)
        self.last_error: str | None = None

    # ---------- Provider 链构建 ----------
    @staticmethod
    def _normalize(cfg: dict) -> list[dict]:
        """合成 provider 链：新 providers 列表优先；否则由 legacy 单槽合成。

        cloud 条目必须 base+key+model 齐全，缺任一即整条丢弃（宁缺毋错）；
        local 条目 model 可空（走 LlmEngine 默认模型）。
        """
        out: list[dict] = []
        raw = cfg.get("providers")
        if isinstance(raw, list) and raw:
            for i, p in enumerate(raw):
                if not isinstance(p, dict):
                    continue
                name = str(p.get("name") or f"p{i + 1}")
                ptype = str(p.get("type")
                            or ("local" if name.lower() == "local" else "cloud")
                            ).strip().lower()
                entry = {
                    "name": name,
                    "type": ptype,
                    "api_base": str(p.get("api_base") or "").rstrip("/"),
                    "api_key": str(p.get("api_key") or ""),
                    "model": str(p.get("model") or ""),
                    "timeout_sec": float(p.get("timeout_sec") or 20),
                }
                if ptype == "cloud":
                    if not (entry["api_base"] and entry["api_key"] and entry["model"]):
                        continue
                    out.append(entry)
                elif ptype == "local":
                    out.append(entry)
        if not out:
            # legacy 单槽合成（v0.6 兼容）：auto=云→本地
            mode = str(cfg.get("provider") or "auto").strip().lower()
            cloud = cfg.get("cloud") or {}
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

    def chain(self) -> list[dict]:
        """当前生效的降级链（legacy provider=cloud/local 时按类型过滤）。"""
        if self._mode in ("cloud", "local"):
            return [p for p in self.providers if p["type"] == self._mode]
        return list(self.providers)

    def cloud_configured(self) -> bool:
        """是否存在已配置的云 provider（自检页判定用）。"""
        return any(p["type"] == "cloud" for p in self.providers)

    # ---------- 可用性 ----------
    def available(self) -> str | None:
        """返回链中将使用的首个 provider 名（'deepseek'/'local'/...）或 None。

        cloud 视配置即用；local 需 LlmEngine 探活。与 v0.6 语义一致
        （旧返回 'cloud'/'local'，新返回配置里的 name）。
        """
        for p in self.chain():
            if p["type"] == "cloud":
                return p["name"]
            try:
                from core.llm_engine import LlmEngine
                if LlmEngine(model=p["model"] or None).available():
                    return p["name"]
            except Exception:  # noqa: BLE001 本地探活失败试下一家
                continue
        return None

    # ---------- 通道调用（v2.1：裸 urllib 云端退役，改经统一 ChatClient）----------
    def _chat_cloud(self, p: dict, system: str, user: str,
                    timeout: float | None = None) -> dict | None:
        """云端档：经 ChatClient（openai SDK）显式指定本 provider，
        降级由 extract_hazard 链控制（方法名/签名保留，测试缝不变）。"""
        result = get_chat_client().chat(
            system, user, json_schema={"type": "object"},
            max_tokens=1024,
            total_deadline_sec=float(timeout or p["timeout_sec"]),
            provider=p["name"])
        if result.status != "failed" and isinstance(result.content, dict):
            return result.content
        self.last_error = (f"[{p['name']}] {result.error or '云端输出无 JSON'}")
        return None

    def _chat_local(self, p: dict, system: str, user: str) -> dict | None:
        """本地档：经 ChatClient 委托 LlmEngine（方法名/签名保留）。"""
        result = get_chat_client().chat(
            system, user, json_schema={"type": "object"},
            max_tokens=1024,
            total_deadline_sec=float(p["timeout_sec"]),
            provider=p["name"])
        if result.status != "failed" and isinstance(result.content, dict):
            return result.content
        self.last_error = (f"[{p['name']}] {result.error or 'local 未返回 JSON'}")
        return None

    def _call(self, p: dict, system: str, user: str,
              timeout: float | None = None) -> dict | None:
        """单 provider 调用（测试注入口：monkeypatch 本方法即可模拟链路）。"""
        if p["type"] == "cloud":
            return self._chat_cloud(p, system, user, timeout)
        return self._chat_local(p, system, user)

    # ---------- 提取 ----------
    @staticmethod
    def build_prompt(text: str, keys: set[str], scenes: list[str]) -> tuple[str, str]:
        system = ("你是工地安全隐患信息提取器。只输出一个 JSON 对象，"
                  "字段严格限定，不确定填 null，禁止编造，禁止输出解释。")
        user = (
            f"从下面的隐患描述中提取字段：\n{text!r}\n"
            f"hazard_key 只能取：{sorted(keys)}\n"
            f"scene_id 只能取：{scenes}\n"
            '输出形如：{"hazard_key":"flammable","scene_id":"hot_work",'
            '"description":"原文精简描述","location":"位置或null"}')
        return system, user

    def extract_hazard(self, text: str) -> dict | None:
        """提取四字段草稿：沿链试各 provider，白名单校验不过/全败 → None。

        total_deadline_sec 为全链总预算：剩余时间不足即停，防多家慢超时叠加。
        """
        self.last_error = None
        text = (text or "").strip()
        if not text:
            self.last_error = "空输入"
            return None
        keys, scenes = _whitelist()
        system, user = self.build_prompt(text, keys, scenes)
        chain = self.chain()
        if not chain:
            self.last_error = "无可用 Provider（enhance 未配置）"
            return None
        t0 = time.monotonic()
        for p in chain:
            remaining = self.total_deadline_sec - (time.monotonic() - t0)
            if remaining <= 0:
                self.last_error = (f"[{p['name']}] 增强链总预算耗尽"
                                   f"（>{self.total_deadline_sec:.0f}s）")
                return None
            out = self._call(p, system, user, timeout=min(p["timeout_sec"], remaining))
            if out is None:
                continue  # 降级下一档
            hk = out.get("hazard_key") or out.get("cls")
            sc = out.get("scene_id") or out.get("scene")
            desc = out.get("description") or out.get("desc") or ""
            loc = out.get("location") or ""
            if hk not in keys:
                self.last_error = f"[{p['name']}] hazard_key 越白名单: {hk!r}"
                continue                      # 白名单外→试下一 provider
            if sc not in scenes:
                self.last_error = f"[{p['name']}] scene_id 越白名单: {sc!r}"
                continue
            return {"hazard_key": hk, "scene_id": sc,
                    "description": str(desc)[:300], "location": str(loc)[:80]}
        return None

    # ---------- 通用单轮 chat（Agent 测试场按 base 对比润色用）----------
    def chat(self, provider_name: str, system: str, user: str,
             num_predict: int | None = None) -> str | None:
        """指定 provider 的通用单轮对话（经统一 ChatClient），
        返回文本或 None（last_error 留因）。"""
        p = next((x for x in self.providers if x["name"] == provider_name), None)
        if p is None:
            self.last_error = f"未知 provider: {provider_name}"
            return None
        result = get_chat_client().chat(
            system, user,
            max_tokens=int(num_predict or 1024),
            total_deadline_sec=float(p["timeout_sec"]),
            provider=provider_name)
        if result.status == "failed":
            self.last_error = (f"[{provider_name}] "
                               f"{result.error or '调用失败'}")
            return None
        if not isinstance(result.content, str):
            self.last_error = f"[{provider_name}] 非文本输出"
            return None
        return result.content.strip() or None

    # ---------- 通道连通性自检（v0.8）----------
    def check_provider(self, p: dict) -> dict:
        """单个 provider 连通性自检：cloud 最小 chat 验 端点+key+模型；
        local 探活 LlmEngine。返回 {name, ok, status, detail, cost_ms}。"""
        result: dict = {"name": p["name"], "ok": False, "status": "error",
                        "detail": "", "cost_ms": 0}
        if p["type"] == "local":
            try:
                from core.llm_engine import LlmEngine
                eng = LlmEngine(model=p["model"] or None)
            except Exception as exc:  # noqa: BLE001
                result["detail"] = f"配置读取失败：{exc}"[:120]
                return result
            if not eng._enabled:
                result.update(ok=True, status="disabled",
                              detail="llm.enabled=false（润色/预填走降级）")
                return result
            if eng.available():
                result.update(ok=True, status="ok", detail=f"{eng.model} 可调用")
            else:
                result["detail"] = "Ollama/模型不可达（润色与预填将降级）"
            return result
        if not (p["api_base"] and p["api_key"] and p["model"]):
            result["status"] = "unconfigured"
            result["detail"] = "未配置（api_base/api_key/model）"
            return result
        body = {
            "model": p["model"],
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
            "temperature": 0,
        }
        t0 = time.monotonic()
        try:
            req = urllib.request.Request(
                f"{p['api_base']}/chat/completions",
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {p['api_key']}"},
                method="POST")
            with urllib.request.urlopen(
                    req, timeout=min(p["timeout_sec"], 15.0)) as resp:
                resp.read()
            result.update(
                ok=True, status="ok", cost_ms=int((time.monotonic() - t0) * 1000),
                detail=f"{p['model']} 可用")
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                result["detail"] = f"key 无效或无权限（HTTP {exc.code}）"
            elif exc.code == 404:
                result["detail"] = (f"模型 {p['model']} 不存在或"
                                    "端点路径不对（HTTP 404，确认 api_base 含 /v1）")
            else:
                result["detail"] = f"服务端拒绝（HTTP {exc.code}）"
        except Exception as exc:  # noqa: BLE001 网络/超时/代理等一律可读呈现
            result["detail"] = f"不可达：{type(exc).__name__}: {exc}"[:120]
        return result

    def check_cloud(self) -> dict:
        """兼容入口（v0.8 前单槽语义）：检查链中首个云 provider。"""
        p = next((x for x in self.providers if x["type"] == "cloud"), None)
        if p is None:
            return {"name": "cloud", "ok": False, "status": "unconfigured",
                    "detail": "未配置（enhance.cloud/providers）", "cost_ms": 0}
        return self.check_provider(p)

    def check_all(self) -> list[dict]:
        """逐 provider 自检（自检页每 provider 一行）。"""
        return [self.check_provider(p) for p in self.chain()]
