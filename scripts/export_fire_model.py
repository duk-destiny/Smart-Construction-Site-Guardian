"""导出真实动火/火情模型为 ONNX。

torch.load 无法处理含单引号的用户目录路径（k'k），故先将 best.pt 复制到
无引号临时路径 C:/_fire_best.pt 再加载导出，最后把 ONNX 复制回项目。

源：data/runs/fire/weights/best.pt（FIRE_BEST_PT 环境变量可覆盖）
类别（datasets/fire-8/data.yaml）：0=Fire, 1=default, 2=smoke
输出：data/models/yolov8_fire_smoke_v2.onnx
"""
from __future__ import annotations

import os
import shutil

from ultralytics import YOLO

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.environ.get(
    "FIRE_BEST_PT",
    r"data/runs/fire/weights/best.pt",
)
import tempfile
TMP_PT = os.path.join(tempfile.gettempdir(), "_fire_best.pt")
OUT_ONNX = os.path.join(ROOT, "data", "models", "yolov8_fire_smoke_v2.onnx")


def main() -> None:
    if not os.path.exists(SRC):
        raise SystemExit(f"源权重不存在: {SRC}")
    # torch.load 无法读含引号路径，先复制到无引号临时路径
    shutil.copy(SRC, TMP_PT)
    print(f"已复制到临时路径: {TMP_PT}")

    model = YOLO(TMP_PT)
    exported = model.export(format="onnx", imgsz=640, dynamic=False,
                            simplify=True, opset=11)
    print(f"导出完成: {exported}")

    if str(exported) != OUT_ONNX:
        shutil.copy(str(exported), OUT_ONNX)
    print(f"ONNX 已复制到: {OUT_ONNX}")
    try:
        os.remove(TMP_PT)
    except OSError:
        pass

    names = getattr(model.model, "names", None)
    print(f"模型类别名: {names}")


if __name__ == "__main__":
    main()
