"""配置加载：读取 config/config.yaml，提供全局配置与场景配置查询。

依赖方向：core 为最底层，不依赖 services/agents/ui（见代码规范 §3）。
所有模型路径、知识库路径、风险矩阵、抽帧参数、LLM 开关均经此模块读取，
禁止在业务代码中硬编码（代码规范 §6）。
"""
from __future__ import annotations

import os
from typing import Any

import yaml


class ConfigError(Exception):
    """配置缺失或解析失败时抛出，由调用方转为降级状态（不阻断进程）。"""


class ConfigLoader:
    """加载 YAML 配置，支持全局读取、按场景(scene_id)取配置与点路径取值。"""

    def __init__(self, path: str = "config/config.yaml") -> None:
        self._path = path
        self._cache: dict[str, Any] | None = None

    def load(self) -> dict[str, Any]:
        """读取并缓存全局配置；文件缺失或 YAML 非法时抛 ConfigError。"""
        if self._cache is None:
            if not os.path.exists(self._path):
                raise ConfigError(f"配置文件不存在: {self._path}")
            with open(self._path, encoding="utf-8") as f:
                self._cache = yaml.safe_load(f)
        return self._cache

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
