"""YOLO .pt → ONNX 转换脚本（需在 ultralytics 可用的环境中运行）。
    
前置：pip install ultralytics onnx onnxruntime
用法：python scripts/convert_yolo.py
输出：data/models/yolov8n_fire.onnx、data/models/yolov8s_fire.onnx
"""
import sys, os
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent  # 仓库根（相对，便于他人复用）
MODELS = PROJECT / "data" / "models"
MODELS.mkdir(parents=True, exist_ok=True)

try:
    from ultralytics import YOLO
except ImportError:
    print("请先安装 ultralytics: pip install ultralytics")
    sys.exit(1)

def convert(pt_name: str, onnx_name: str) -> None:
    pt = MODELS / pt_name
    if not pt.exists():
        print(f"  {pt_name} 不存在，跳过")
        return
    print(f"加载 {pt_name}...")
    model = YOLO(str(pt))
    nc = model.model.model[-1].nc  # type: ignore[union-attr]
    print(f"  类别数: {nc}")
    out = model.export(format="onnx", opset=11, imgsz=640, simplify=True)
    print(f"  输出: {out}")

if __name__ == "__main__":
    print("转换 yolov8n.pt → yolov8n_fire.onnx ...")
    convert("yolov8n.pt", "yolov8n_fire.onnx")
    print("转换 yolov8s.pt → yolov8s_fire.onnx ...")
    convert("yolov8s.pt", "yolov8s_fire.onnx")
    print("完成！")
