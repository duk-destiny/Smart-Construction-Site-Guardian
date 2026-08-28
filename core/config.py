"""配置加载：读取 config/config.yaml，提供全局配置与场景配置查询。

依赖方向：core 为最底层，不依赖 services/agents/ui（见代码规范 §3）。
所有模型路径、知识库路径、风险矩阵、抽帧参数、LLM 开关均经此模块读取，
禁止在业务代码中硬编码（代码规范 §6）。
v0.8：字符串值支持 ${ENV_VAR} / ${ENV_VAR:-默认值} 环境变量展开——
API key、webhook 等敏感值可经环境注入，不必明文写进入 git 的 config.yaml；
未定义且无默认值的占位保持原样（便于日志中一眼看出漏配）。
"""
from __future__ import annotations

import os
import re
from typing import Any

import yaml

# ${VAR} 或 ${VAR:-default}；VAR 名与 shell 同约束（字母/下划线开头）
_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _expand_env_str(text: str) -> str:
    """对单个字符串做环境变量展开；未定义且无默认值的占位保持原样。"""
    def _sub(m: re.Match) -> str:
        val = os.environ.get(m.group(1))
        if val is not None:
            return val
        default = m.group(2)
        return default if default is not None else m.group(0)
    return _ENV_PATTERN.sub(_sub, text)


def _expand_env(node: Any) -> Any:
    """递归展开配置树中所有字符串值里的 ${ENV} 占位。"""
    if isinstance(node, dict):
        return {k: _expand_env(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_expand_env(v) for v in node]
    if isinstance(node, str):
        return _expand_env_str(node)
    return node


class ConfigError(Exception):
    """配置缺失或解析失败时抛出，由调用方转为降级状态（不阻断进程）。"""


class ConfigLoader:
    """加载 YAML 配置，支持全局读取、按场景(scene_id)取配置与点路径取值。"""

    def __init__(self, path: str = "config/config.yaml") -> None:
        self._path = path
        self._cache: dict[str, Any] | None = None

    def load(self) -> dict[str, Any]:
        """读取并缓存全局配置；文件缺失或 YAML 非法时抛 ConfigError。

        加载后对全部字符串值做一次 ${ENV_VAR} 环境变量展开（v0.8），
        敏感值（api_key/webhook_url）可经环境注入而无需写入配置文件。
        """
        if self._cache is None:
            if not os.path.exists(self._path):
                raise ConfigError(f"配置文件不存在: {self._path}")
            with open(self._path, encoding="utf-8") as f:
                self._cache = _expand_env(yaml.safe_load(f))
            self._validate(self._cache)
        return self._cache

    @staticmethod
    def _validate(cfg: Any) -> None:
        """轻量结构校验：notify/monitor 段类型与必需键。不阻断极简配置（段缺失即跳过）。"""
        if cfg is None:
            return
        if not isinstance(cfg, dict):
            raise ConfigError("配置根节点必须是字典")
        notify = cfg.get("notify")
        if notify is not None:
            if not isinstance(notify, dict):
                raise ConfigError("notify 配置段必须是字典")
            if notify.get("enabled") and not str(
                    notify.get("webhook_url", "")).strip():
                raise ConfigError("notify.enabled=true 时 webhook_url 不能为空")
        monitor = cfg.get("monitor")
        if monitor is not None:
            if not isinstance(monitor, dict):
                raise ConfigError("monitor 配置段必须是字典")
            sources = monitor.get("sources")
            if sources is not None and not isinstance(sources, list):
                raise ConfigError("monitor.sources 必须是列表")

    def get_scene(self, scene_id: str) -> dict[str, Any]:
        """按场景 ID 取该场景专属配置（权重/知识库/规则矩阵）。"""
        scenes = self.load().get("scenes", {})
        if scene_id not in scenes:
            raise ConfigError(f"未知场景: {scene_id}")
        return scenes[scene_id]

    def get(self, key: str, default: Any = None) -> Any:
        """通过点路径（如 "infer.conf_thres"）安全取值，缺失返回 default。"""
        node: Any = self.load()
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node


# ---- 进程级共享实例（Phase 1）----
# 热路径（实时帧合规、LLM 引擎构造等）此前各建 ConfigLoader 实例，
# 同一 YAML 被反复解析；shared() 提供进程级缓存实例消除重复读盘。
# 显式 path 的构造不受影响（测试注入用）；测试需隔离时用 reset_shared()。
_SHARED: dict[str, "ConfigLoader"] = {}


def shared_config(path: str = "config/config.yaml") -> "ConfigLoader":
    """按 path 取进程级共享 ConfigLoader（首建后缓存）。"""
    loader = _SHARED.get(path)
    if loader is None:
        loader = ConfigLoader(path)
        _SHARED[path] = loader
    return loader


def reset_shared() -> None:
    """清空共享实例（测试隔离用）。"""
    _SHARED.clear()
