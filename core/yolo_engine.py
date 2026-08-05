"""YOLO 推理引擎：本地 ONNX Runtime 推理（CPU EP），零外网依赖（C1）。

类别处理（数据驱动，支持任意 YOLOv8 导出权重）：
- `load()` 自动读取 ONNX 元数据中的 `names` 得到模型真实类别名（如 Fire/default/smoke）；
- 通过 `class_map`（配置提供，模型类名→项目隐患键）将检测结果映射到项目白名单；
  映射为 None 的类别（如 default）直接丢弃；
- 未提供 class_map 时退回 4 类白名单按索引映射（兼容原 4 类动火专用模型）。
"""
from __future__ import annotations

import ast
import os

import cv2
import numpy as np
import onnxruntime as ort

# 项目隐患白名单（C4）。多场景共用：
#  - 动火/火情：spark=火花/动火明火；face_shield=防护面罩（已佩戴）；
#    extinguisher=灭火器（已检测到）；flammable=易燃物未清理；smoke=烟雾（火情）
#  - 施工 PPE（industrial-safety-vision 路线）：helmet/no_helmet、vest/no_vest、person
#  - 堆放物（Detecting-danger 独门能力）：load_object、load_object_tilted
WHITELIST = [
    "spark", "smoke", "face_shield", "extinguisher", "flammable",
    "helmet", "no_helmet", "vest", "no_vest", "person",
    "load_object", "load_object_tilted",
]
WHITELIST_CN = {
    "spark": "火花（动火明火）",
    "face_shield": "防护面罩",
    "extinguisher": "灭火器",
    "flammable": "周边易燃物未清理",
    "smoke": "烟雾（火情）",
    "helmet": "佩戴安全帽",
    "no_helmet": "未佩戴安全帽",
    "vest": "穿着反光衣",
    "no_vest": "未穿反光衣",
    "person": "人员",
    "load_object": "堆放物",
    "load_object_tilted": "堆放物倾斜/不规范（坠落风险）",
}

# 火情类（用于视觉 Agent 的"未检出火情目标"提示判定）
FIRE_CLASSES = {"spark", "smoke"}

# 默认输入尺寸（仅当 ONNX 输入形状为动态/无法解析时回退使用）
_DEFAULT_INPUT_SIZE = 640


def _parse_names(meta_str: str | None) -> list[str] | None:
    """解析 ONNX 元数据中的 names（形如 "{0: 'Fire', 1: 'default', 2: 'smoke'}"）。"""
    if not meta_str:
        return None
    try:
        d = ast.literal_eval(meta_str)
        if isinstance(d, dict):
            return [d[k] for k in sorted(d, key=lambda x: int(x))]
    except (ValueError, SyntaxError, TypeError):
        pass
    return None


# 进程级 ONNX 会话缓存：按 (绝对路径, intra_op上限) 复用 InferenceSession，
# 避免离线任务链每任务重载同一权重（RealtimeEngine / VisionAgent / 自检页共享）。
_SESSIONS: dict[tuple[str, int], "ort.InferenceSession"] = {}


def _get_session(onnx_path: str, intra_op_threads: int | None) -> "ort.InferenceSession":
    """按 (绝对路径, intra_op上限) 缓存并复用 InferenceSession。

    多个 YoloEngine 实例加载同一权重时共享同一 InferenceSession（run() 线程安全），
    省去每任务重读磁盘+重建会话的开销；不同 intra_op 上限视为不同会话（封顶是
    创建期参数，不可跨 cap 复用，故 RealtimeEngine(cap=5) 与 VisionAgent(默认) 各占一槽）。
    """
    cap = int(intra_op_threads) if intra_op_threads and intra_op_threads > 0 else 0
    key = (os.path.abspath(onnx_path), cap)
    sess = _SESSIONS.get(key)
    if sess is None:
        so = ort.SessionOptions()
        if cap > 0:
            so.intra_op_num_threads = cap
        sess = ort.InferenceSession(
            onnx_path, so, providers=["CPUExecutionProvider"])
        _SESSIONS[key] = sess
    return sess


class YoloEngine:
    """本地 YOLOv8 ONNX 推理引擎。"""

    def __init__(self, conf_thres: float = 0.45,
                 iou_thres: float = 0.45,
                 class_map: dict[str, str | None] | None = None) -> None:
        self.conf_thres = conf_thres
        self.iou_thres = iou_thres
        self.class_map = class_map
        self.session: ort.InferenceSession | None = None
        self.input_name: str | None = None
        self.input_size: tuple[int, int] = (_DEFAULT_INPUT_SIZE, _DEFAULT_INPUT_SIZE)
        self.class_names: list[str] | None = None

    def load(self, onnx_path: str, intra_op_threads: int | None = None) -> None:
        """加载 ONNX 权重；文件缺失抛 FileNotFoundError（由上层转 failed）。

        intra_op_threads: 每个会话 intra-op 线程上限。多头并行时按 cpu//引擎数
        分核，避免多个 InferenceSession 同时各吃满核导致抢核反而变慢；None/0=自动。
        会话按 (绝对路径, intra_op上限) 进程级缓存，RealtimeEngine/VisionAgent/自检页
        加载同一权重时复用，避免离线任务链每任务重载 ONNX（见 _get_session）。
        """
        if not os.path.exists(onnx_path):
            raise FileNotFoundError(f"YOLO 权重缺失: {onnx_path}")
        self.session = _get_session(onnx_path, intra_op_threads)
        self.input_name = self.session.get_inputs()[0].name
        # 读取模型真实输入尺寸（避免与训练/导出 imgsz 不一致，如 416 vs 640）
        shape = self.session.get_inputs()[0].shape
        if len(shape) >= 4 and isinstance(shape[2], int) and isinstance(shape[3], int):
            self.input_size = (shape[2], shape[3])
        else:
            self.input_size = (_DEFAULT_INPUT_SIZE, _DEFAULT_INPUT_SIZE)
        meta = self.session.get_modelmeta().custom_metadata_map
        self.class_names = _parse_names(meta.get("names"))

    def infer(self, img_path: str) -> list[dict]:
        """对单张图片推理，返回映射后的检测结果列表。

        Returns: [{"cls","conf","bbox":[cx,cy,w,h]}, ...]（坐标为模型输入空间）
        """
        if self.session is None:
            raise RuntimeError("YoloEngine 未加载权重，请先 load()")
        if not os.path.exists(img_path):
            raise FileNotFoundError(f"图片不存在: {img_path}")
        blob = self._preprocess(img_path)
        raw = self.session.run(None, {self.input_name: blob})[0]  # [1, C, N]
        return self._decode(raw)

    def infer_frame(self, frame: np.ndarray) -> list[dict]:
        """对单帧（numpy BGR）推理，返回映射到原图坐标的检测结果。

        实时摄像头态使用：坐标 [cx,cy,w,h] 已按原图尺寸还原，可直接绘图。
        """
        if self.session is None:
            raise RuntimeError("YoloEngine 未加载权重，请先 load()")
        if frame is None or frame.size == 0:
            return []
        h0, w0 = frame.shape[:2]
        ih, iw = self.input_size
        blob = self._preprocess_frame(frame)
        raw = self.session.run(None, {self.input_name: blob})[0]  # [1, C, N]
        dets = self._decode(raw)
        # 将模型输入空间坐标还原到原图尺寸
        sx, sy = w0 / iw, h0 / ih
        for d in dets:
            cx, cy, w, h = d["bbox"]
            d["bbox"] = [round(cx * sx, 1), round(cy * sy, 1),
                         round(w * sx, 1), round(h * sy, 1)]
        return dets

    def _preprocess(self, img_path: str) -> np.ndarray:
        img = cv2.imread(img_path)
        if img is None:
            raise ValueError(f"图片读取失败: {img_path}")
        return self._preprocess_frame(img)

    def _preprocess_frame(self, frame: np.ndarray) -> np.ndarray:
        h, w = self.input_size
        img = cv2.resize(frame, (w, h))
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        img = img.astype(np.float32) / 255.0
        blob = np.transpose(img, (2, 0, 1))[None, ...]  # [1,3,H,W]
        return blob

    def _decode(self, raw: np.ndarray) -> list[dict]:
        """解码 YOLOv8 输出 [1, C, N]（C=4+nclass）为检测结果。

        1) 由 ONNX 元数据得到模型真实类名；2) 经 class_map 映射到项目隐患键
        （None 丢弃）；3) 无 class_map 时退回按索引的 4 类白名单映射；
        4) 经 NMS 去除重叠框。
        """
        preds = raw[0]  # [C, N]
        boxes = preds[:4, :].T  # [N,4] cx,cy,w,h
        scores = preds[4:, :]   # [nclass, N]
        candidates: list[tuple[int, float, int, list[float]]] = []
        for i in range(preds.shape[1]):
            cls_id = int(scores[:, i].argmax())
            conf = float(scores[cls_id, i])
            if conf < self.conf_thres:
                continue
            # 解析模型真实类名
            if self.class_names is not None and cls_id < len(self.class_names):
                raw_name = self.class_names[cls_id]
            elif cls_id < len(WHITELIST):
                raw_name = WHITELIST[cls_id]
            else:
                raw_name = f"cls{cls_id}"
            # 映射到项目隐患键
            if self.class_map is not None:
                mapped = self.class_map.get(raw_name)
                if mapped is None:
                    continue  # 白名单外/忽略类（如 default）
                cls = mapped
            else:
                if cls_id >= len(WHITELIST):
                    continue
                cls = WHITELIST[cls_id]
            cx, cy, w, h = (float(v) for v in boxes[i])
            candidates.append((i, conf, cls, [cx, cy, w, h]))

        if not candidates:
            return []

        # NMS：按类别独立进行，避免不同类互相抑制
        keep_indices = set()
        by_cls: dict[str, list[tuple[int, float, list[float]]]] = {}
        for i, conf, cls, box in candidates:
            by_cls.setdefault(cls, []).append((i, conf, box))
        for cls, items in by_cls.items():
            indices = [it[0] for it in items]
            confs = [it[1] for it in items]
            bboxes = [it[2] for it in items]
            # cv2.dnn.NMSBoxes 需要 [x1,y1,x2,y2]
            x1y1x2y2 = [
                [x - w / 2, y - h / 2, x + w / 2, y + h / 2]
                for x, y, w, h in bboxes
            ]
            kept = cv2.dnn.NMSBoxes(x1y1x2y2, confs, self.conf_thres, self.iou_thres)
            # OpenCV 不同版本返回 tuple 或 np.ndarray
            if isinstance(kept, tuple):
                kept = kept[0] if kept else []
            for k in kept:
                keep_indices.add(indices[int(k)])

        out: list[dict] = []
        for i, conf, cls, box in candidates:
            if i in keep_indices:
                out.append({
                    "cls": cls,
                    "conf": round(conf, 3),
                    "bbox": [round(v, 1) for v in box],
                })
        return out
