"""INT8 量化脚本测试（v0.6 二期d）。

依赖仓库随附的 FP32 权重；验证：量化产物体积显著缩小、onnxruntime 可加载、
注册幂等且 active=0（不顶替线上）。无权重环境自动跳过。
"""
from __future__ import annotations

import os

import pytest

SRC = os.path.join("data", "models", "yolov8_fire_smoke_v2.onnx")

pytestmark = pytest.mark.skipif(
    not os.path.exists(SRC), reason="仓库未随附 FP32 权重")


@pytest.fixture(scope="module")
def quantized(tmp_path_factory):
    from scripts.quantize_models import quantize_one
    out_dir = tmp_path_factory.mktemp("int8")
    return quantize_one(__import__("pathlib").Path(SRC), out_dir), \
        tmp_path_factory.mktemp("db")


def test_output_smaller_and_loadable(quantized):
    dst, _ = quantized
    import onnxruntime as ort
    assert dst.exists()
    assert dst.stat().st_size * 3 < os.path.getsize(SRC)      # 至少 ~3x 缩
    sess = ort.InferenceSession(str(dst), providers=["CPUExecutionProvider"])
    assert sess.get_inputs()[0].name


def test_register_idempotent_and_inactive(quantized):
    dst, db_dir = quantized
    from pathlib import Path
    from scripts.quantize_models import register
    db = str(Path(db_dir) / "app.db")
    mid1 = register(db, Path(SRC), dst)
    mid2 = register(db, Path(SRC), dst)
    assert mid1 == mid2                                        # 幂等
    import sqlite3
    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT active, version FROM model_registry WHERE id=?", (mid1,)
    ).fetchone()
    conn.close()
    assert row[0] == 0                                         # 不顶替线上
    assert row[1].endswith("-int8")
