"""项目路径锚点（Phase 1）：统一 BASE_DIR，任意 cwd 启动不破。

dao/db.py 原本锚在包目录，而 core/evidence.py、services/notify_service.py
等散落着 cwd 相对路径（"data/alarms" 等）——换目录启动即写错位置。
统一约定：
- 磁盘读写一律经 data_dir()/BASE_DIR 解析为绝对路径；
- 数据库/推送 URL 中仍存 **相对项目根的 posix 路径**（如 data/alarms/x.jpg），
  展示/读取边界经 resolve() 还原。
"""
from __future__ import annotations

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"


def data_path(*parts: str) -> str:
    """data/ 下相对片段 → 绝对路径字符串（不自动创建目录）。"""
    return str(DATA_DIR.joinpath(*parts))


def to_rel(abs_or_rel: str) -> str:
    """绝对路径（或已是相对路径）→ 相对项目根的 posix 路径，供入库/URL。"""
    p = Path(abs_or_rel)
    if not p.is_absolute():
        return str(p).replace("\\", "/")
    try:
        return p.resolve().relative_to(BASE_DIR).as_posix()
    except ValueError:  # 项目根之外（异常场景）原样 posix 化
        return p.as_posix()


def resolve(rel_or_abs: str) -> str:
    """入库的相对路径 → 绝对路径；已是绝对路径原样返回。"""
    p = Path(rel_or_abs)
    return str(p if p.is_absolute() else BASE_DIR / p)
