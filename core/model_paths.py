"""模型权重路径解析（Phase 1）：模型切换状态唯一落 DB（model_registry.active）。

此前 ModelService.switch 会正则回写 config.yaml —— 既污染入 git 的配置，
又与 ${ENV} 展开相互踩踏。新语义：config 仍是场景权重清单的基线，
DB active 行按模型族（fire/ppe）覆盖同族路径；引擎构建/reload 时经
本模块解析生效。core 层实现，agents/core 均可直接使用（dao 为最底层）。
"""
from __future__ import annotations

import os

from core.paths import resolve as _resolve
from dao.models import ModelRegistryDAO


def family_of(path: str) -> str | None:
    """从权重文件名推断模型族名（与 bootstrap 种子口径一致）：
    yolov8_fire_smoke_v2.onnx → fire；ppe_yolov8_v3.onnx → ppe。"""
    fname = os.path.basename(str(path)).lower()
    if "fire" in fname:
        return "fire"
    if "ppe" in fname:
        return "ppe"
    return None


def active_weight_overrides() -> dict[str, str]:
    """查询 model_registry，返回 {模型族: 权重绝对路径}。

    只收录 active=1 且文件真实存在的条目；注册表缺失/查询失败时返回空
    （引擎按纯 config 构建，行为与 Phase 1 之前一致）。
    """
    overrides: dict[str, str] = {}
    conn = None
    try:
        from dao.db import get_conn
        conn = get_conn()
        rows = ModelRegistryDAO(conn).list_all(limit=500)
        for row in rows:
            if not row["active"] or not row["path"]:
                continue
            family = family_of(row["path"]) or row["name"]
            path = _resolve(row["path"])
            if os.path.exists(path):
                overrides.setdefault(family, path)
    except Exception:  # noqa: BLE001 解析失败不阻断引擎构建
        return {}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
    return overrides


def apply_overrides(specs: list[dict], overrides: dict[str, str]) -> list[dict]:
    """把 active 覆盖映射应用到场景权重 spec 列表（纯函数，便于单测）。

    spec: [{"path": "...", "class_map": {...}}]；返回新列表，不原地改写。
    """
    out: list[dict] = []
    for spec in specs or []:
        if not isinstance(spec, dict) or not spec.get("path"):
            continue
        new_spec = dict(spec)
        family = family_of(spec["path"])
        if family and family in overrides:
            new_spec["path"] = overrides[family]
        out.append(new_spec)
    return out
