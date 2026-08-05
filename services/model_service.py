"""模型版本注册与切换服务。"""
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
        self._dao.set_active(name, model_id)

    def list_models(self, limit: int = 200) -> list:
        return self._dao.list_all(limit=limit)

    def active_model(self, name: str):
        return self._dao.get_active(name)
