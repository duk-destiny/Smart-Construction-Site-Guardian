# -*- coding: utf-8 -*-
"""E2E 临时后端启动器：用独立数据库跑后端，不碰生产 data/app.db。

原理：dao.db.get_conn 在运行时读模块级 DEFAULT_DB_PATH（dao/db.py 注释明确
支持此替换方式），在 import api.main 之前改指向即可——所有后续
`from dao.db import DEFAULT_DB_PATH` 绑定都拿到新路径。
测试完成后删除 data/tmp_e2e_test.db* 即可销毁全部测试数据。
"""
import os
import sys
from pathlib import Path

# 仓库根：无论从哪个工作目录启动，都保证能导入 dao / api
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

os.environ["API_PREWARM"] = "0"  # 跳过 YOLO/监控/LLM 预热，首请求按需加载

TMP_DB = str(ROOT / "data" / "tmp_e2e_test.db")

import dao.db  # noqa: E402
dao.db.DEFAULT_DB_PATH = TMP_DB

if __name__ == "__main__":
    import uvicorn
    from api.main import app
    print(f"[e2e-launcher] using temp DB: {TMP_DB}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
