"""本地 4070 训练 PPE 检测模型，导出 ONNX 到 data/models/ppe_yolov8.onnx。

用法（在 venv313 中，项目根目录下）：
  venv313/Scripts/python.exe scripts/train_ppe_local.py

特性：
  - 自动修正 data.yaml 的 path（指向数据集绝对目录，避免 Ultralytics 解析到 cwd）
  - 支持断点续训：若 runs/train/ppe/weights/last.pt 已存在，则从其继续，
    即使训练进程被中途终止，重跑本脚本也会接着上次进度，不会从头再来。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import shutil
import torch
import yaml
from huggingface_hub import snapshot_download
from ultralytics import YOLO

# 实时刷新日志（重定向到文件时默认块缓冲，进程被杀会丢失末尾进度）
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent  # scripts/ -> 项目根
DATA_MODELS = ROOT / "data" / "models"
DATASET_DIR = ROOT / "data" / "raw" / "ppe_dataset"
# 注意：用户名含单引号(k'k)，Ultralytics 读 .pt 绝对路径时会吞掉单引号导致找不到文件。
# 因此训练产物(权重/导出)全部放到无特殊字符的路径 C:/ppe_runs，彻底规避该问题。
RUN_DIR = Path("C:/ppe_runs/ppe")
WEIGHTS_DIR = RUN_DIR / "weights"


def main() -> None:
    # 关闭 HuggingFace 的 Xet 传输（避免触发 429 限流的 xet-read-token 接口）
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    os.environ.setdefault("HF_HUB_ENABLE_HF_TRANSFER", "0")

    assert torch.cuda.is_available(), "CUDA 不可用，无法用 GPU 训练"
    print("使用 GPU:", torch.cuda.get_device_name(0), flush=True)

    # 1) 下载数据集（断点续传，已下载的会跳过）
    DATASET_DIR.parent.mkdir(parents=True, exist_ok=True)
    ds = snapshot_download(
        repo_id="LibreYOLO/construction-safety-gsnvb",
        repo_type="dataset",
        local_dir=str(DATASET_DIR),
        allow_patterns=["data.yaml", "train/*", "valid/*"],
        max_workers=4,
    )

    # 2) 修正 data.yaml 的 path 为数据集绝对目录
    data_yaml = os.path.join(ds, "data.yaml")
    with open(data_yaml, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg["path"] = str(DATASET_DIR)
    fixed_yaml = os.path.join(ds, "data_fixed.yaml")
    with open(fixed_yaml, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True)
    data_yaml = fixed_yaml
    print("数据集:", ds, " data:", data_yaml, flush=True)

    # 3) 续训检测
    last_pt = WEIGHTS_DIR / "last.pt"
    if last_pt.exists():
        print("发现已有权重，从 last.pt 续训:", last_pt, flush=True)
        model = YOLO(str(last_pt))
        train_kwargs = dict(resume=True)
    else:
        print("从头训练（yolov8n 预训练权重）", flush=True)
        model = YOLO("yolov8n.pt")
        train_kwargs = dict(
            data=data_yaml,
            epochs=50,
            imgsz=416,
            batch=16,
            device=0,
            project="C:/ppe_runs",
            name="ppe",
            exist_ok=True,
            verbose=True,
        )

    model.train(**train_kwargs)

    # 4) 导出 ONNX（先导出到无特殊字符路径，再拷回 data/models）
    best = WEIGHTS_DIR / "best.pt"
    if not best.exists():
        best = last_pt
    exported = YOLO(str(best)).export(format="onnx", imgsz=416, opset=17)
    src = Path(exported) if os.path.exists(exported) else (RUN_DIR / "ppe_yolov8.onnx")
    DATA_MODELS.mkdir(parents=True, exist_ok=True)
    dst = DATA_MODELS / "ppe_yolov8.onnx"
    shutil.copy(src, dst)
    print("已导出 ONNX:", dst, flush=True)


if __name__ == "__main__":
    main()
