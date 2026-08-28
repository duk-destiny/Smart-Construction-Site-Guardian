"""实时轻链路检测引擎（A3）：复用现有 YOLO/PPE 检测头，对单帧做轻量研判。

与上传态的"多 Agent 重链路"不同，实时态只做 检测 → 三级合规，
不调用 RAG / 不生成工单，以满足低延迟连续监测。
实时初始场景：construction_ppe + hot_work 同时接入（各自检测头复用现有权重）。
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from typing import Iterable

import cv2
import numpy as np

from core.compliance import evaluate
from core.config import ConfigLoader
from core.false_positive import filter_ppe_contradiction, filter_smoke_vest_conflict
from core.tracker import IoUTracker
from core.yolo_adapter import COCO_CN
from core.yolo_engine import WHITELIST_CN, YoloEngine
from core.logging import get_logger
log = get_logger(__name__)


def _compute_intra_op(cfg, n_engines: int) -> int:
    # 多头并行按 cpu//引擎数 封顶 intra-op 线程，防抢核（小模型线程宜少）
    _cfg_intra = int(cfg.get("infer.intra_op_threads", 0) or 0)
    if _cfg_intra > 0:
        return _cfg_intra
    _cpu = os.cpu_count() or 2
    if n_engines >= 2:
        return max(1, _cpu // (2 * n_engines))
    return max(1, _cpu // 2)


class RealtimeEngine:
    """构建并缓存各场景检测头，对单帧执行联合检测 + 三级合规研判。"""

    def __init__(self, scenes: Iterable[str] = ("construction_ppe", "hot_work")) -> None:
        self.cfg = ConfigLoader()
        self.engines: list[tuple[str, YoloEngine]] = []
        self.tracker = IoUTracker()
        # 检测头并行：onnxruntime run() 释放 GIL，多头用线程池并行跑。为防多 session
        # 抢核，每引擎 intra_op 封顶 ≈ 物理核/引擎数 = logical//(2*引擎数)（HT 机）；
        # 单头则用满物理核(≈logical//2)。实测：线程过多反而更慢（小模型同步开销）。
        scenes_list = list(scenes)
        self._scenes = list(scenes)  # 供 reload() 重建时复用同一场景集
        self._intra_op_threads = _compute_intra_op(self.cfg, len(scenes_list))
        self._build(scenes_list)

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
                    eng.load(path, intra_op_threads=self._intra_op_threads)
                    self.engines.append((sid, eng))
                except Exception as e:  # noqa: BLE001 单头缺失优雅跳过
                    log.warning(f"跳过不可用模型 {path}: {e}")

    @property
    def available(self) -> bool:
        return bool(self.engines)

    def reload(self) -> None:
        # 模型切换后热重载：重读 config 并重建引擎（复用 _SESSIONS 会话缓存）。
        # 不重启进程即可让实时页/后台监控用上 DB active 指向的新模型；tracker 一并
        # 重置，避免跨模型 track_id 串扰。供 page_admin 切换按钮后调用。
        self.cfg = ConfigLoader()  # 丢弃旧 _cache，重读 config.yaml
        self._intra_op_threads = _compute_intra_op(self.cfg, len(self._scenes))
        self.engines = []
        self.tracker = IoUTracker()
        self._build(list(self._scenes))

    @staticmethod
    def _tag(dets: list[dict], sid: str) -> list[dict]:
        """给检测项打场景与中文描述标签（原地改写并返回）。"""
        for d in dets:
            d["scene"] = sid
            d["violation_desc"] = WHITELIST_CN.get(
                d.get("cls"), COCO_CN.get(d.get("cls"), d.get("cls")))
        return dets

    def detect(self, frame: np.ndarray) -> list[dict]:
        """对一帧执行全部检测头，返回合并后的检测结果（坐标已还原到原图）。"""
        if frame is None or frame.size == 0:
            return []
        detections: list[dict] = []
        # 多头并行：onnxruntime run() 释放 GIL，用线程池把各头"求和"变"取最大"；
        # 单头或会话缺失时走串行。每会话 intra_op 已按 cpu//引擎数封顶防抢核。
        if len(self.engines) > 1:
            with ThreadPoolExecutor(max_workers=len(self.engines)) as pool:
                futs = [(sid, eng, pool.submit(eng.infer_frame, frame))
                        for sid, eng in self.engines]
                for sid, eng, fut in futs:
                    try:
                        dets = fut.result()
                    except Exception as e:  # noqa: BLE001
                        log.warning(f"推理失败 {sid}: {e}")
                        continue
                    detections.extend(self._tag(dets, sid))
        else:
            for sid, eng in self.engines:
                try:
                    dets = eng.infer_frame(frame)
                except Exception as e:  # noqa: BLE001
                    log.warning(f"推理失败 {sid}: {e}")
                    continue
                detections.extend(self._tag(dets, sid))
        return detections

    def analyze(self, frame: np.ndarray) -> dict:
        """检测 + 三级合规研判，返回 (detections, compliance)。

        实时不变量（安全系统第一原则·实时性）：critical 帧必须当帧出警——
        analyze 与告警之间不得插入 LLM/RAG/工单等阻塞推理；本方法全程纯规则
        （检测→误报过滤→跟踪→三级合规），首帧 critical 即返回，无多帧确认门控。
        """
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
