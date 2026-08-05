"""实时轻链路检测引擎（A3）：复用现有 YOLO/PPE/堆放物检测头，对单帧做轻量研判。

与上传态的"多 Agent 重链路"不同，实时态只做 检测 → 三级合规，
不调用 RAG / 不生成工单，以满足低延迟连续监测。
实时初始场景：construction_ppe + hot_work 同时接入（各自检测头复用现有权重）。
"""
from __future__ import annotations

from typing import Iterable

import cv2
import numpy as np

from core.compliance import evaluate
from core.config import ConfigLoader
from core.false_positive import filter_ppe_contradiction, filter_smoke_vest_conflict
from core.load_object_detector import LoadObjectDetector
from core.tracker import IoUTracker
from core.yolo_adapter import COCO_CN
from core.yolo_engine import WHITELIST_CN, YoloEngine
from core.logging import get_logger
log = get_logger(__name__)


class RealtimeEngine:
    """构建并缓存各场景检测头，对单帧执行联合检测 + 三级合规研判。"""

    def __init__(self, scenes: Iterable[str] = ("construction_ppe", "hot_work")) -> None:
        self.cfg = ConfigLoader()
        self.engines: list[tuple[str, YoloEngine]] = []
        self.lod: LoadObjectDetector | None = None
        self.tracker = IoUTracker()
        self._build(list(scenes))

    def _build(self, scenes: list[str]) -> None:
        conf = self.cfg.get("infer.conf_thres", 0.45)
        iou = self.cfg.get("infer.iou_thres", 0.45)
        for sid in scenes:
            try:
                scene = self.cfg.get_scene(sid)
            except Exception as e:  # noqa: BLE001 场景缺失不应拖垮实时页
                log.warning(f"跳过未知场景 {sid}: {e}")
                continue
            scene_conf = scene.get("conf_thres", conf)
            for spec in scene.get("yolo_weights", []) or []:
                path = spec.get("path")
                try:
                    eng = YoloEngine(conf_thres=scene_conf, iou_thres=iou,
                                     class_map=spec.get("class_map"))
                    eng.load(path)
                    self.engines.append((sid, eng))
                except Exception as e:  # noqa: BLE001 单头缺失优雅跳过
                    log.warning(f"跳过不可用模型 {path}: {e}")
            # 堆放物倾斜检测（Detecting-danger 独门能力，按场景开关，仅接入一次）
            lod_cfg = scene.get("load_object_detection", {}) or {}
            if lod_cfg.get("enabled") and self.lod is None:
                try:
                    self.lod = LoadObjectDetector(lod_cfg)
                except Exception as e:  # noqa: BLE001
                    log.warning(f"堆放物检测不可用: {e}")

    @property
    def available(self) -> bool:
        return bool(self.engines) or self.lod is not None

    def detect(self, frame: np.ndarray) -> list[dict]:
        """对一帧执行全部检测头，返回合并后的检测结果（坐标已还原到原图）。"""
        if frame is None or frame.size == 0:
            return []
        detections: list[dict] = []
        for sid, eng in self.engines:
            try:
                dets = eng.infer_frame(frame)
            except Exception as e:  # noqa: BLE001
                log.warning(f"推理失败 {sid}: {e}")
                continue
            for d in dets:
                d["scene"] = sid
                d["violation_desc"] = WHITELIST_CN.get(
                    d["cls"], COCO_CN.get(d["cls"], d["cls"]))
            detections.extend(dets)
        if self.lod is not None:
            try:
                dets = self.lod.detect_and_assess_frame(frame)
                for d in dets:
                    d["scene"] = "construction_ppe"
                    d["violation_desc"] = WHITELIST_CN.get(d["cls"], d["cls"])
                detections.extend(dets)
            except Exception as e:  # noqa: BLE001
                log.warning(f"堆放物推断失败: {e}")
        return detections

    def analyze(self, frame: np.ndarray) -> dict:
        """检测 + 三级合规研判，返回 (detections, compliance)。"""
        dets = self.detect(frame)
        dets, _ = filter_smoke_vest_conflict(dets)
        dets, _ = filter_ppe_contradiction(dets)
        dets = self.tracker.update(dets)
        return dets, evaluate(dets)

    def reset_tracking(self) -> None:
        self.tracker.reset()

    @staticmethod
    def draw(frame: np.ndarray, compliance: dict) -> np.ndarray:
        """在帧上绘制检测框：违规红框（B1 红色高亮）/ 警告黄框 / 安全绿框。"""
        out = frame.copy()
        for item in compliance.get("violations", []):
            x, y, w, h = [float(v) for v in item["bbox"]]
            x1, y1 = int(x - w / 2), int(y - h / 2)
            x2, y2 = int(x + w / 2), int(y + h / 2)
            color = (229, 57, 53) if item["severity"] == "critical" else (251, 192, 45)
            thickness = 3 if item["severity"] == "critical" else 2
            cv2.rectangle(out, (x1, y1), (x2, y2), color, thickness)
            label = f"{item['label']} {item['conf']:.2f}"
            if item.get("track_id") is not None:
                label += f" #{item['track_id']}"
            cv2.putText(out, label, (x1, max(y1 - 6, 12)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
        for item in compliance.get("safe", []):
            x, y, w, h = [float(v) for v in item["bbox"]]
            x1, y1 = int(x - w / 2), int(y - h / 2)
            x2, y2 = int(x + w / 2), int(y + h / 2)
            cv2.rectangle(out, (x1, y1), (x2, y2), (67, 160, 71), 2)
            if item.get("track_id") is not None:
                cv2.putText(out, f"#{item['track_id']}", (x1, max(y1 - 6, 12)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (67, 160, 71), 2)
        return out
