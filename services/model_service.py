"""模型版本注册与切换服务。"""
from __future__ import annotations

import os
import re
import sqlite3

from core.config import ConfigLoader
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
        # 让 config.yaml 与 DB active 同步：切换后把新版本路径回写 config，
        # 运行中的引擎经 reload() 即可拾取新模型（见 page_admin 切换按钮）。
        new = self._dao.get_active(name)
        # sqlite3.Row 无 .get()，用索引访问（path 列恒存在）
        if new is not None and new["path"]:
            self._sync_config_path(name, new["path"])

    def list_models(self, limit: int = 200) -> list:
        return self._dao.list_all(limit=limit)

    def active_model(self, name: str):
        return self._dao.get_active(name)


    def _sync_config_path(self, name: str, new_path: str) -> None:
        # 族名正则替换回写 config（保留注释）：从新路径文件名提族名（如
        # yolov8_fire_smoke），把 config 里 data/models/<族>_v*.onnx 整体替换为新
        # （相对）路径。DB 存的可能是绝对路径，先规范化为相对 repo 根。失败不阻断
        # 切换（DB active 已生效，重启后 reload 仍可拾取）。
        try:
            new_rel = self._to_rel_path(new_path)
            m = re.match(r"^(.+)_v\d+\.onnx$", os.path.basename(new_rel))
            if not m:
                return
            stem = m.group(1)
            cfg_path = ConfigLoader()._path
            with open(cfg_path, encoding="utf-8") as f:
                text = f.read()
            pattern = re.compile(r"data/models/" + re.escape(stem) + r"_v\d+\.onnx")
            new_text, n = pattern.subn(new_rel, text)
            if n and new_text != text:
                with open(cfg_path, "w", encoding="utf-8", newline="") as f:
                    f.write(new_text)
        except Exception:
            pass

    @staticmethod
    def _to_rel_path(p: str) -> str:
        # 绝对路径（DB 里 v3 是绝对路径）规范化为相对 cwd 的 posix 路径。
        return os.path.relpath(p, os.getcwd()).replace("\\", "/")
