"""切换 model_registry 中某个模型的 active 版本。"""
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
    parser.add_argument("--model-id", required=True)
    args = parser.parse_args()

    conn = get_conn()
    init_db(conn)
    svc = ModelService(conn)
    svc.switch(args.name, args.model_id)
    active = svc.active_model(args.name)
    print(f"已切换 {args.name} 到: {active['version'] if active else 'N/A'}")
    print(f"路径: {active['path'] if active else 'N/A'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
