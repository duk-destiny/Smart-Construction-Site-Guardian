"""YOLO 类别适配层：兼容 n 类标准权重与 4 类定制权重。
    
本系统的白名单（C4）固定为 spark/face_shield/extinguisher/flammable 4 类。
YOLO 原权重可能是 80 类（COCO）或其它 n 类，此模块负责：
1. 记录实际 nclass
2. 将 80 类 → 按白名单顺序重新映射（需外部提供类别映射表）
3. 或直接使用 4 类定制权重（无需映射）
"""
from __future__ import annotations

# COCO 80 类标准类名（与 ultralytics YOLOv8 导出顺序一致）。
# 用于"标准权重"模式：输出真实 COCO 类名，不进行 4 类白名单过滤。
COCO_NAMES: list[str] = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck",
    "boat", "traffic light", "fire hydrant", "stop sign", "parking meter", "bench",
    "bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra",
    "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee",
    "skis", "snowboard", "sports ball", "kite", "baseball bat", "baseball glove",
    "skateboard", "surfboard", "tennis racket", "bottle", "wine glass", "cup",
    "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch",
    "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse",
    "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink",
    "refrigerator", "book", "clock", "vase", "scissors", "teddy bear", "hair drier",
    "toothbrush",
]

# COCO 类名 → 中文释义（用于视觉 Agent 输出可读描述；未列出的回退英文原名）。
COCO_CN: dict[str, str] = {
    "person": "现场人员（监火人须全程在岗）",
    "fire hydrant": "消防栓",
    "extinguisher": "灭火器",  # COCO 无此类，保留占位以防定制权重
    "bottle": "瓶罐（可能为易燃容器）",
    "backpack": "背包",
    "chair": "座椅",
    "couch": "沙发",
    "cell phone": "手机",
    "laptop": "笔记本电脑",
    "book": "书籍",
}

# COCO 80 类 → 白名单 4 类的映射规则（用于标准 YOLO 权重）。
# 注意：COCO 没有火花/防护面罩/灭火器/易燃物类，真正的动火隐患识别
# 需要动火专用 4 类模型（data/models/yolov8n_fire.onnx）。
COCO_TO_WHITELIST: dict[int, str] = {
    # 0: person → 监火人需在岗（仅作"人员在场"信号，不视为隐患）
}

# 如果 YOLO 权重输出类别数 ∈ {4, 5, 6, 80, ...}，此函数判定策略
def resolve_nclass(nclass: int) -> str:
    """根据输出类别数返回策略标记。"""
    if nclass == 4:
        return "custom_4cls"  # 直接映射：0→spark, 1→face_shield...
    elif nclass == 80:
        return "coco_80cls"   # 需要 COCO_TO_WHITELIST 映射
    else:
        return f"unknown_{nclass}cls"
