"""AI 提取预填服务（v0.6 二期a）：自由文本 → 四字段草稿（双 Provider 降级链）。

Provider 顺序（config `enhance.provider`）：auto=云→本地；cloud/local 单选。
- 云：OpenAI 兼容 /chat/completions（用户自配 key，数据出境提示由 UI 承担）；
- 本地：Ollama qwen3:8b（LlmEngine.ask_json 封闭集管线复用）。

铁律：输出**仅作表单预填草稿**，人工确认后才建单；风险定级仍由
compliance.severity 查表完成——LLM 全程不碰判定路径（Q3/Q6）。
双 Provider 皆未配置/调用失败 → 返回 None（表单手填兜底），last_error 留痕。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from core.config import ConfigLoader


def _whitelist() -> tuple[set[str], list[str]]:
    """(隐患键白名单, 场景清单)——均以运行时配置为准。"""
    from core.compliance import SEVERITY
    keys = {k for k, v in SEVERITY.items() if k != "none" and v != "safe"}
    scenes = list((ConfigLoader().get("scenes") or {}).keys())
    return keys, scenes


class EnhanceEngine:
    """四字段提取器：{hazard_key, scene_id, description, location}。"""

    def __init__(self, provider: str | None = None) -> None:
        try:
            cfg = ConfigLoader().get("enhance") or {}
        except Exception:  # noqa: BLE001 配置缺失=纯手填模式
            cfg = {}
        self.provider = provider or cfg.get("provider") or "auto"
        cloud = cfg.get("cloud") or {}
        self.cloud_base = (cloud.get("api_base") or "").rstrip("/")
        self.cloud_key = cloud.get("api_key") or ""
        self.cloud_model = cloud.get("model") or "gpt-4o-mini"
        self.timeout = float(cloud.get("timeout_sec") or 20)
        self.last_error: str | None = None

    # ---------- 可用性 ----------
    def available(self) -> str | None:
        """返回将使用的 provider（'cloud'/'local'）或 None（静默）。"""
        order = self._chain()
        return order[0] if order else None

    def _chain(self) -> list[str]:
        if self.provider == "cloud":
            return ["cloud"] if self._cloud_ok() else []
        if self.provider == "local":
            return ["local"] if self._local_ok() else []
        out = []
        if self._cloud_ok():
            out.append("cloud")
        if self._local_ok():
            out.append("local")
        return out

    def _cloud_ok(self) -> bool:
        return bool(self.cloud_base and self.cloud_key)

    def _local_ok(self) -> bool:
        try:
            from core.llm_engine import LlmEngine
            return LlmEngine().available()
        except Exception:  # noqa: BLE001
            return False

    # ---------- 通道调用 ----------
    def _chat_cloud(self, system: str, user: str) -> dict | None:
        body = {
            "model": self.cloud_model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": 0.2,
        }
        try:
            req = urllib.request.Request(
                f"{self.cloud_base}/chat/completions",
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {self.cloud_key}"},
                method="POST")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            raw = (data["choices"][0]["message"].get("content") or "").strip()
            s, e = raw.find("{"), raw.rfind("}")
            if s == -1 or e <= s:
                self.last_error = "云端输出无 JSON"
                return None
            obj = json.loads(raw[s:e + 1])
            return obj if isinstance(obj, dict) else None
        except Exception as exc:  # noqa: BLE001 云端失败→降级本地
            self.last_error = f"cloud {type(exc).__name__}: {exc}"
            return None

    def _chat_local(self, system: str, user: str) -> dict | None:
        try:
            from core.llm_engine import LlmEngine
            out = LlmEngine().ask_json(f"{system}\n{user}")
            if out is None:
                self.last_error = "local 未返回 JSON"
            return out
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"local {type(exc).__name__}: {exc}"
            return None

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
        """提取四字段草稿；白名单校验不过/双 provider 失败 → None。"""
        self.last_error = None
        text = (text or "").strip()
        if not text:
            self.last_error = "空输入"
            return None
        keys, scenes = _whitelist()
        system, user = self.build_prompt(text, keys, scenes)

        for prov in self._chain():
            out = self._chat_cloud(system, user) if prov == "cloud" \
                else self._chat_local(system, user)
            if out is None:
                continue  # 降级下一档
            hk = out.get("hazard_key") or out.get("cls")
            sc = out.get("scene_id") or out.get("scene")
            desc = out.get("description") or out.get("desc") or ""
            loc = out.get("location") or ""
            if hk not in keys:
                self.last_error = f"hazard_key 越白名单: {hk!r}"
                continue                      # 白名单外→试下一 provider
            if sc not in scenes:
                self.last_error = f"scene_id 越白名单: {sc!r}"
                continue
            return {"hazard_key": hk, "scene_id": sc,
                    "description": str(desc)[:300], "location": str(loc)[:80]}
        return None
