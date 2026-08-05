"""仅导出 ONNX：从 C:/ppe_runs/ppe/weights/best.pt 导出到 data/models/ppe_yolov8_v2.onnx。

训练已完成，无需重新训练。在 venv313 中运行（项目根目录下）：
  venv313/Scripts/python.exe scripts/export_ppe_onnx.py
"""
import shutil
from pathlib import Path

from ultralytics import YOLO

ROOT = Path(__file__).resolve().parent.parent  # scripts/ -> 项目根
WEIGHTS = Path("C:/ppe_runs/ppe/weights/best.pt")
DATA_MODELS = ROOT / "data" / "models"

exported = YOLO(str(WEIGHTS)).export(format="onnx", imgsz=416, opset=17, simplify=True)
src = Path(exported)
DATA_MODELS.mkdir(parents=True, exist_ok=True)
dst = DATA_MODELS / "ppe_yolov8_v2.onnx"
shutil.copy(src, dst)
print("已导出 ONNX:", dst, flush=True)
