"""首次启动自举：建库 + 种子默认账号，保证 clone 后开箱即登录。

评委拉取仓库后首次 ``streamlit run app.py`` 即自动建表并写入默认账号；
v0.2 起升级为**按用户逐个补种**（缺哪个补哪个，幂等）：老演示库升级到 v0.2
后也能自动获得整改责任人账号 lisi，而不会触碰既有账号的密码与角色。
默认账号仅用于演示，上线前请修改密码。
"""
from __future__ import annotations

import bcrypt

from dao.db import get_conn, init_db

# 默认账号（演示用）：admin 全权限；safety 上传研判受限；
# responsible 为整改责任人（v0.2 工单闭环的接收端）。
_DEFAULT_USERS = (
    # (username, password, role)
    ("admin", "admin123", "admin"),
    ("safety", "demo1234", "safety"),
    ("lisi", "demo1234", "responsible"),
)


def ensure_initialized() -> None:
    """建库 + 按用户补种默认账号。幂等：存在的用户（含改过密码的）一律跳过。"""
    conn = get_conn()
    try:
        init_db(conn)
        for username, pwd, role in _DEFAULT_USERS:
            if conn.execute(
                "SELECT 1 FROM users WHERE username=?", (username,)
            ).fetchone():
                continue
            pwd_hash = bcrypt.hashpw(
                pwd.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
            conn.execute(
                "INSERT INTO users(id, username, pwd_hash, role, created_at) "
                "VALUES (?, ?, ?, ?, datetime('now'))",
                ("u_" + username, username, pwd_hash, role))
        conn.commit()
    finally:
        conn.close()


def _ensure_models() -> None:
    """种子模型注册表：扫描 data/models/*.onnx，从 config.yaml 读取活跃路径。

    幂等：已有同名同版本不重复插入；config 引用的模型标 active=1。
    仅在 model_registry 表为空时执行（首次启动 / 清库后）。
    """
    import os, re
    try:
        from core.config import ConfigLoader
        from dao.models import ModelRegistryDAO
        conn = get_conn()
        try:
            init_db(conn)
            # 仅空表时种子
            if conn.execute("SELECT COUNT(*) FROM model_registry").fetchone()[0] > 0:
                return
            # 读 config 获取活跃模型路径
            cfg = ConfigLoader().load()
            active_paths = set()
            for scene in (cfg.get("scenes") or {}).values():
                for w in (scene.get("yolo_weights") or []):
                    p = w.get("path")
                    if p:
                        active_paths.add(p.replace("/", os.sep))
            # 也读顶层 yolo_onnx（兜底火情模型）
            top = cfg.get("models", {}).get("yolo_onnx")
            if top:
                active_paths.add(top.replace("/", os.sep))

            dao = ModelRegistryDAO(conn)
            models_dir = "data/models"
            if not os.path.isdir(models_dir):
                return
            for fname in sorted(os.listdir(models_dir)):
                if not fname.endswith(".onnx"):
                    continue
                rel_path = f"data/models/{fname}"
                # 从文件名提取 name 和 version
                # yolov8_fire_smoke_v2.onnx -> name=fire, version=v2
                # ppe_yolov8_v3.onnx -> name=ppe, version=v3
                if "fire" in fname:
                    name = "fire"
                elif "ppe" in fname:
                    name = "ppe"
                else:
                    continue
                vm = re.search(r"_v(\d+)\.onnx$", fname)
                if not vm:
                    continue
                version = f"v{vm.group(1)}"
                is_active = rel_path.replace("/", os.sep) in active_paths or rel_path in active_paths
                # data_yaml 路径推断
                data_yaml = f"data/combined/{name}/data.yaml"
                dao.insert(
                    name=name, version=version, path=rel_path,
                    data_yaml=data_yaml if os.path.exists(data_yaml) else None,
                    imgsz=640, active=is_active,
                    notes="启动自动注册" if is_active else None)
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def ensure_models() -> None:
    """公开入口：种子模型注册表（幂等，仅空表时执行）。"""
    _ensure_models()