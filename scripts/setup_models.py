"""下载本地 BGE 中文 Embedding 模型到 data/models/（评委首次部署运行一次）。

用法：python scripts/setup_models.py
需联网；下载完成后离线可用。模型用于 RAG 规范检索的查询向量化。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 与 config.yaml 的 models.bge_dir / core/rag_engine._DEFAULT_BGE 一致
BGE_DIR = (ROOT / "data" / "models" / "BAAI--bge-small-zh-v1.5"
           / "snapshots" / "master")


def main() -> None:
    if BGE_DIR.exists() and any(BGE_DIR.iterdir()):
        print(f"BGE 模型已存在：{BGE_DIR}")
        return
    BGE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("缺少 huggingface_hub，请先 pip install huggingface_hub")
        sys.exit(1)
    print("下载 BAAI/bge-small-zh-v1.5 到", BGE_DIR, "...")
    snapshot_download(
        repo_id="BAAI/bge-small-zh-v1.5",
        local_dir=str(BGE_DIR),
        local_dir_use_symlinks=False,
    )
    print(f"完成：{BGE_DIR}")


if __name__ == "__main__":
    main()