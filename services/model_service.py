"""模型版本注册与切换服务。

Phase 1：**模型切换状态唯一落 DB**（model_registry.active），不再运行时
正则回写 config.yaml。生效路径见 core/model_paths.py：引擎构建/reload
时按 DB active 覆盖 config 同族路径，切换后 reload() 即拾取。
"""
from __future__ import annotations

import sqlite3

from dao.models import ModelRegistryDAO


class ModelService:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._dao = ModelRegistryDAO(conn)

    def register(self, name: str, version: str, path: str,
                 data_yaml: str | None = None, imgsz: int | None = None,
                 mAP50: float | None = None, mAP50_95: float | None = None,
                 notes: str | None = None, active: bool = False) -> str:
        return self._dao.insert(
            name=name, version=version, path=path, data_yaml=data_yaml,
            imgsz=imgsz, mAP50=mAP50, mAP50_95=mAP50_95, notes=notes,
            active=1 if active else 0)

    def switch(self, name: str, model_id: str) -> None:
        """切换活跃版本：仅写 DB active 标志（唯一事实源）。

        运行中的引擎由调用方触发 reload()，重建时经 core/model_paths
        解析拾取 DB active 指向的新路径。
        """
        self._dao.set_active(name, model_id)

    def list_models(self, limit: int = 200) -> list:
        return self._dao.list_all(limit=limit)

    def active_model(self, name: str):
        return self._dao.get_active(name)
