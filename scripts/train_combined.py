"""训练合并后的 PPE / 火情模型，并导出 ONNX。

训练产物：
  data/runs_combined/ppe/...
  data/runs_combined/fire/...
  data/models/ppe_yolov8_v2.onnx
  data/models/yolov8_fire_smoke_v2.onnx
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from pathlib import Path

import os
import torch
from ultralytics import YOLO
from ultralytics.engine.trainer import BaseTrainer
import ultralytics.nn.tasks as nn_tasks


def _read_results_csv_without_polars(self) -> dict:
    """本地兼容补丁：Ultralytics 默认用 polars 读 results.csv，在这台机器会因 CPU 指令集报错。"""
    return {}


BaseTrainer.read_results_csv = _read_results_csv_without_polars
BaseTrainer.plot_metrics = lambda self: None


_ORIG_TORCH_SAFE_LOAD = nn_tasks.torch_safe_load


def _torch_safe_load_fixed(weight, *args, **kwargs):
    """修复 Ultralytics 在 k'k 用户目录下丢失单引号的 Windows 路径 bug。"""
    if isinstance(weight, (str, os.PathLike)):
        path_str = os.fspath(weight)
        marker = "hzz-fire-safety"
        if marker in path_str:
            idx = path_str.find(marker)
            correct_root = str(ROOT)
            path_str = correct_root + path_str[idx + len(marker):]
        if os.path.exists(path_str):
            return torch.load(path_str, map_location="cpu",
                              weights_only=False), path_str
    return _ORIG_TORCH_SAFE_LOAD(weight, *args, **kwargs)


nn_tasks.torch_safe_load = _torch_safe_load_fixed

ROOT = Path(__file__).resolve().parent.parent
PRETRAINED_SRC = ROOT / "yolov8s.pt"
MODEL_YAML = "yolov8s.yaml"
PROJECT = ROOT / "data" / "runs_combined"

JOBS = {
    "ppe": {
        "data": str(ROOT / "data/combined/ppe/data.yaml"),
        "epochs": 60,
        "batch": 16,
        "imgsz": 640,
        "run_name": "ppe_s",
        "skip_existing": True,
        "out": "data/models/ppe_yolov8_v2.onnx",
    },
    "fire": {
        "data": str(ROOT / "data/combined/fire/data.yaml"),
        "epochs": 60,
        "batch": 16,
        "imgsz": 640,
        "run_name": "fire_s",
        "skip_existing": True,
        "out": "data/models/yolov8_fire_smoke_v2.onnx",
    },
}


def _resolve_data_yaml(name: str, job: dict) -> str:
    """优先使用合并主数据集，缺失时回退到已确认反馈样本集。"""
    combined = ROOT / "data" / "combined" / name / "data.yaml"
    feedback = ROOT / "data" / "feedback_training" / "yolo" / name / "data.yaml"
    if combined.exists():
        return str(combined)
    if feedback.exists():
        print(f"[{name}] 未找到合并数据集，使用反馈样本训练集: {feedback}", flush=True)
        return str(feedback)
    raise SystemExit(
        f"[{name}] 缺少训练数据: {combined} 或 {feedback}；"
        "请先运行 prepare_combined_dataset.py 或 prepare_feedback_training.py"
    )


def _output_path(name: str, version: str) -> Path:
    if name == "ppe":
        return ROOT / "data" / "models" / f"ppe_yolov8_{version}.onnx"
    return ROOT / "data" / "models" / f"yolov8_fire_smoke_{version}.onnx"


def _read_metrics(run_dir: Path) -> dict:
    """从 Ultralytics results.csv 读取最后一行 mAP。"""
    results = run_dir / "results.csv"
    if not results.exists():
        return {}
    try:
        with open(results, encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        if not rows:
            return {}
        last = rows[-1]
        def _num(key):
            try:
                return float(last.get(key))
            except (TypeError, ValueError):
                return None
        return {
            "mAP50": _num("metrics/mAP50(B)"),
            "mAP50_95": _num("metrics/mAP50-95(B)"),
        }
    except Exception:  # noqa: BLE001 指标缺失不阻塞导出
        return {}


def _model_from_ckpt(path: Path):
    """直接 torch.load 加载 best.pt，绕开 Ultralytics 的用户目录路径 bug。"""
    ckpt = torch.load(str(path), map_location="cpu", weights_only=False)
    src_model = ckpt.get("ema") or ckpt.get("model")
    if src_model is None:
        raise RuntimeError(f"checkpoint 缺少 model/ema: {path}")
    model = YOLO(MODEL_YAML)
    model.model = src_model
    model.model.task = "detect"
    if hasattr(model.model, "args") and not isinstance(model.model.args, dict):
        model.model.args = vars(model.model.args)
    model.ckpt = ckpt
    return model


def _train_and_export(name: str, job: dict,
                      from_best: bool = False,
                      version: str = "v3") -> dict:
    data_yaml = _resolve_data_yaml(name, job)
    run_name = job.get("run_name", name)
    batch = job.get("batch", 16)
    base_best_rel = f"data/runs_combined/{run_name}/weights/best.pt"
    export_name = run_name

    if from_best and Path(base_best_rel).exists():
        export_name = f"{run_name}_ft_{version}"
        print(f"[{name}] 基于现有 best.pt 续训 {job['epochs']} epoch", flush=True)
        model = _model_from_ckpt(Path(base_best_rel))
        model.train(
            data=data_yaml,
            epochs=job["epochs"],
            imgsz=job["imgsz"],
            batch=batch,
            device=0,
            workers=4,
            patience=10,
            seed=0,
            amp=True,
            cos_lr=True,
            close_mosaic=5,
            optimizer="SGD",
            warmup_epochs=1,
            lr0=0.0005,
            lrf=0.1,
            project=str(PROJECT),
            name=export_name,
            exist_ok=True,
            verbose=True,
        )
    elif job.get("skip_existing") and Path(base_best_rel).exists():
        print(f"[{name}] 检测到已完成 best.pt，跳过训练并直接导出", flush=True)
    else:
        if from_best:
            print(f"[{name}] 未找到现有 best.pt，回退到官方预训练权重", flush=True)
        if PRETRAINED_SRC.exists():
            ckpt = torch.load(str(PRETRAINED_SRC), map_location="cpu",
                              weights_only=False)
            model = YOLO(MODEL_YAML)
            model.model.load_state_dict(ckpt["model"].state_dict())
        else:
            print(f"[{name}] 未找到 yolov8s.pt，使用 yolov8s.yaml 从零初始化",
                  flush=True)
            model = YOLO(MODEL_YAML)
        model.train(
            data=data_yaml,
            epochs=job["epochs"],
            imgsz=job["imgsz"],
            batch=batch,
            device=0,
            workers=4,
            patience=20,
            seed=0,
            amp=True,
            cos_lr=True,
            close_mosaic=5,
            project=str(PROJECT),
            name=run_name,
            exist_ok=True,
            verbose=True,
        )

    best_rel = f"data/runs_combined/{export_name}/weights/best.pt"
    if not Path(best_rel).exists():
        raise SystemExit(f"训练完成但未找到 best.pt: {best_rel}")

    model = _model_from_ckpt(Path(best_rel))
    exported = model.export(
        format="onnx", imgsz=job["imgsz"], opset=17, simplify=False)
    dst = _output_path(name, version)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if Path(exported).resolve() != dst.resolve():
        shutil.copy2(exported, dst)
        if Path(exported).name == "yolov8s.onnx":
            try:
                os.remove(exported)
            except OSError:
                pass
    print(f"[{name}] ONNX 已导出: {dst}", flush=True)
    return {
        "name": name,
        "version": version,
        "path": str(dst),
        "run_dir": str(PROJECT / export_name),
        **_read_metrics(PROJECT / export_name),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-best", action="store_true",
                        help="从现有 best.pt 续训，而不是从头训练")
    parser.add_argument("--version", default="v3", help="新模型版本号")
    parser.add_argument("--only", choices=["ppe", "fire"],
                        help="只训练指定场景")
    parser.add_argument("--epochs", type=int, help="覆盖默认 epochs")
    args = parser.parse_args()

    if not PROJECT.exists():
        PROJECT.mkdir(parents=True, exist_ok=True)
    names = [args.only] if args.only else ["ppe", "fire"]
    results: dict = {}
    for name in names:
        job = dict(JOBS[name])
        if args.epochs:
            job["epochs"] = args.epochs
        print(f"[train_combined] 开始训练 {name}", flush=True)
        results[name] = _train_and_export(
            name, job, from_best=args.from_best, version=args.version)
        print(f"[train_combined] 完成 {name}", flush=True)

    result_path = ROOT / "data" / "train" / "last_training_result.json"
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(
        json.dumps({"ok": True, "results": results},
                   ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"TRAIN_RESULT_JSON={result_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
