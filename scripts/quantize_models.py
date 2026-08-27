"""YOLO ONNX INT8 动态量化（v0.6 二期d）。

对 data/models 下的 FP32 权重做 `quantize_dynamic`（权重 int8），输出
`<name>_int8.onnx` 并注册进 model_registry（version=原版本+`-int8`，
active=0——延续 Q7「新模型不自动顶替线上」原则，管理端手动切换）。

CPU 推理预期 2-3x 加速、权重体积约 1/4；逐类精度须以
`scripts/evaluate_models.py`（会自动纳入新注册版本）复核后方可启用。

用法：
    python scripts/quantize_models.py                        # 全部 FP32 权重
    python scripts/quantize_models.py --only yolov8_fire_smoke_v2.onnx
    python scripts/quantize_models.py --no-register          # 只导出不注册
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dao.db import get_conn, init_db                     # noqa: E402


def quantize_one(src: Path, out_dir: Path | None = None) -> Path:
    """量化单个 ONNX，返回输出路径；已存在则直接复用（幂等）。

    在系统临时目录用无特殊字符的工作副本执行——onnx shape-infer 会把
    `-inferred.onnx` 中间文件写回输入同目录，含撇号/中文的用户路径下
    读回会 FileNotFoundError（实测），临时目录可彻底规避。
    """
    import shutil
    import tempfile
    from onnxruntime.quantization import QuantType, quantize_dynamic

    dst = (out_dir or src.parent) / (src.stem + "_int8.onnx")
    if dst.exists():
        return dst
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        tmp_src = work / "model.onnx"
        shutil.copy2(src, tmp_src)
        quantize_dynamic(model_input=str(tmp_src),
                         model_output=str(work / "model_int8.onnx"),
                         weight_type=QuantType.QInt8)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(work / "model_int8.onnx"), str(dst))
    return dst


def version_of(path: Path) -> str:
    m = re.search(r"_v(\d+)", path.stem)
    return (f"v{m.group(1)}-int8") if m else "int8"


def register(db_path: str, src: Path, dst: Path) -> str:
    """注册量化版本（active=0，不顶替线上）；幂等：同 path 已存在则跳过。"""
    conn: sqlite3.Connection = get_conn(db_path)
    init_db(conn)
    try:
        exists = conn.execute(
            "SELECT id FROM model_registry WHERE path=?", (str(dst),)
        ).fetchone()
        if exists:
            return exists["id"]
        from dao.models import ModelRegistryDAO
        mid = ModelRegistryDAO(conn).insert(
            name="fire" if "fire" in src.stem else "ppe",
            version=version_of(src), path=str(dst), active=0,
            notes="INT8 动态量化（quantize_dynamic），上线前须逐类评测复核")
        return mid
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="YOLO ONNX INT8 动态量化")
    ap.add_argument("--only", default=None, help="只处理该文件名（如 yolov8_fire_smoke_v2.onnx）")
    ap.add_argument("--no-register", action="store_true", help="只导出不注册")
    ap.add_argument("--db", default="data/app.db")
    args = ap.parse_args()

    models_dir = ROOT / "data" / "models"
    targets = [p for p in sorted(models_dir.glob("*.onnx"))
               if "_int8" not in p.stem
               and (args.only is None or p.name == args.only)]
    if not targets:
        print("[skip] 无可量化目标（可能全部已量化）")
        return 0

    for src in targets:
        dst = quantize_one(src)
        ratio = src.stat().st_size / max(dst.stat().st_size, 1)
        line = f"{src.name} ({src.stat().st_size/1e6:.1f}MB) -> {dst.name} ({dst.stat().st_size/1e6:.1f}MB, {ratio:.1f}x)"
        if args.no_register:
            print(line)
            continue
        mid = register(args.db, src, dst)
        print(f"{line} | registered id={mid} (active=0)")

    print("[next] 运行 scripts/evaluate_models.py 复核逐类精度后，"
          "再在管理端手动切换。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
