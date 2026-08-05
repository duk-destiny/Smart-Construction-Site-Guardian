"""注册模型版本到 model_registry。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dao.db import get_conn, init_db  # noqa: E402
from services.model_service import ModelService  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--path", required=True)
    parser.add_argument("--data-yaml")
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--map50", type=float)
    parser.add_argument("--map50-95", type=float)
    parser.add_argument("--notes")
    parser.add_argument("--active", action="store_true")
    args = parser.parse_args()

    conn = get_conn()
    init_db(conn)
    mid = ModelService(conn).register(
        name=args.name, version=args.version, path=args.path,
        data_yaml=args.data_yaml, imgsz=args.imgsz,
        mAP50=args.map50, mAP50_95=getattr(args, "map50_95"),
        notes=args.notes, active=args.active)
    print(f"已注册模型: {mid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
