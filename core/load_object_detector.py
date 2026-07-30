"""堆放物检测 + 倾斜判定（移植自 Detecting-danger 仓库的 personload + edge 模块）。

保留 Detecting-danger 的独门能力：
1. YOLOv3 personload 模型检测堆放物（Load 类）；
2. 对检测框裁剪后做 Canny + HoughLinesP，拟合线段斜率；
3. 斜率标准差 > 阈值 → 判定"堆放物倾斜/不规范（坠落风险）"。

仅本地 OpenCV DNN 推理，零外网依赖（C1）。该能力为 Detecting-danger 独有，
industrial-safety-vision 不含，故单独保留。
"""
from __future__ import annotations

import os

import cv2
import numpy as np


class LoadObjectDetector:
    """本地堆放物检测器（YOLOv3 darknet + Hough 倾斜判定）。"""

    def __init__(self, cfg: dict) -> None:
        self.model = cfg.get("model")
        self.config = cfg.get("config")
        self.names = cfg.get("names")
        self.conf_thres = float(cfg.get("conf_thres", 0.1))
        self.nms_thres = float(cfg.get("nms_thres", 0.4))
        self.tilt_std_thres = float(cfg.get("tilt_std_thres", 2.0))
        # personload 模型按 416×416 训练/推理（原仓库 plyolo(size=416)）
        self.input_size = int(cfg.get("input_size", 416))
        self._net = None
        self._classes: list[str] = []

    def _ensure_net(self):
        if self._net is None:
            if not (self.model and self.config
                    and os.path.exists(self.model) and os.path.exists(self.config)):
                raise FileNotFoundError(
                    f"堆放物检测模型缺失: model={self.model}, config={self.config}")
            self._net = cv2.dnn.readNet(self.model, self.config)
            if self.names and os.path.exists(self.names):
                with open(self.names, encoding="utf-8") as f:
                    self._classes = [l.strip() for l in f]
        return self._net

    def _detect_loads(self, img_path: str):
        """返回 (原图 BGR, [(bbox[x,y,w,h], conf), ...])，仅含 Load 类。"""
        img = cv2.imread(img_path)
        if img is None:
            return None, []
        return self._detect_loads_frame(img)

    def _detect_loads_frame(self, img: np.ndarray):
        """对 numpy BGR 帧检测堆放物，返回 [(bbox[x,y,w,h], conf), ...]（仅 Load 类）。"""
        net = self._ensure_net()
        if img is None or img.size == 0:
            return []
        h, w = img.shape[:2]
        blob = cv2.dnn.blobFromImage(img, 1 / 255.0, (self.input_size, self.input_size),
                                     (0, 0, 0), True, crop=False)
        net.setInput(blob)
        layer_names = net.getLayerNames()
        out_layers = [layer_names[i - 1] for i in net.getUnconnectedOutLayers()]
        outs = net.forward(out_layers)

        boxes, confs, cls_ids = [], [], []
        for out in outs:
            for det in out:
                scores = det[5:]
                cid = int(np.argmax(scores))
                conf = float(scores[cid])
                if conf > self.conf_thres:
                    cx, cy, bw, bh = (int(det[0] * w), int(det[1] * h),
                                      int(det[2] * w), int(det[3] * h))
                    boxes.append([int(cx - bw / 2), int(cy - bh / 2), bw, bh])
                    confs.append(conf)
                    cls_ids.append(cid)
        idx = cv2.dnn.NMSBoxes(boxes, confs, self.conf_thres, self.nms_thres)
        loads = []
        if len(idx) > 0:
            for i in np.array(idx).flatten():
                name = self._classes[cls_ids[i]] if self._classes else "Load"
                if name.strip().lower() == "load":
                    loads.append((boxes[i], confs[i]))
        return loads

    @staticmethod
    def _assess_tilt(crop: np.ndarray, std_thres: float) -> bool:
        """Hough 线段斜率标准差 > 阈值 → 倾斜/不规范。"""
        if crop is None or crop.size == 0:
            return False
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
        edges = cv2.Canny(gray, 150, 300)
        lines = cv2.HoughLinesP(edges, 1, np.pi / 180.0, 160,
                                minLineLength=50, maxLineGap=5)
        if lines is None:
            return False
        slopes: list[float] = []
        for ln in lines:
            x1, y1, x2, y2 = ln[0]
            if x1 == x2:
                continue
            slope, _ = np.polyfit([x1, x2], [y1, y2], 1)
            slopes.append(slope)
        if len(slopes) < 2:
            return False
        return float(np.std(slopes)) > std_thres

    def detect_and_assess(self, img_path: str) -> list[dict]:
        """对单图检测堆放物并判定倾斜，返回检测结果列表。"""
        img = cv2.imread(img_path)
        if img is None:
            return []
        return self.detect_and_assess_frame(img)

    def detect_and_assess_frame(self, frame: np.ndarray) -> list[dict]:
        """对单帧（numpy BGR）检测堆放物并判定倾斜，返回检测结果列表。"""
        if frame is None or frame.size == 0:
            return []
        loads = self._detect_loads_frame(frame)
        detections: list[dict] = []
        for (x, y, w, h), conf in loads:
            cy0, cy1 = max(0, y), min(frame.shape[0], y + h)
            cx0, cx1 = max(0, x), min(frame.shape[1], x + w)
            crop = frame[cy0:cy1, cx0:cx1]
            tilted = self._assess_tilt(crop, self.tilt_std_thres)
            cls = "load_object_tilted" if tilted else "load_object"
            detections.append({
                "cls": cls,
                "conf": round(conf, 3),
                "bbox": [round(x, 1), round(y, 1), round(w, 1), round(h, 1)],
            })
        return detections
