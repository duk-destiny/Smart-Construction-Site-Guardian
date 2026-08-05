"""视觉 Agent（M03）：调用本地 YOLO 引擎逐帧检测，映射违规中文描述。

仅本地推理、零外网（C1）；检测类别白名单由 YoloEngine 保证（C4）。
支持按场景加载多个检测头（如 fire 头 + PPE 头），并可在场景配置中
启用 Detecting-danger 独有的"堆放物倾斜检测"。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from agents.base import AgentBase, AgentMessage
from core.config import ConfigLoader
from core.yolo_adapter import COCO_CN
from core.yolo_engine import FIRE_CLASSES, WHITELIST_CN, YoloEngine
from core.logging import get_logger
log = get_logger(__name__)

# 正向安全信号：检测到这些类别说明防护到位，不应进入违规描述
SAFE_SIGNAL_CLASSES = {"helmet", "vest", "person"}

if TYPE_CHECKING:
    pass


class VisionAgent(AgentBase):
    """视觉检测 Agent：输入 image_paths，输出 detections + violation_descs。

    yolo 注入时（测试/复用）仅用该单引擎；否则按 scene_id 从配置构建检测头。
    """

    def __init__(self, yolo: YoloEngine | None = None, scene_id: str | None = None) -> None:
        self.yolo = yolo
        self.scene_id = scene_id

    def _build_engines(self, cfg: ConfigLoader) -> list[YoloEngine]:
        """按场景配置构建检测头列表；缺权重/不可用则跳过（优雅降级）。"""
        if not self.scene_id:
            # 兜底：单火情模型（兼容旧行为 / 测试）
            eng = YoloEngine(
                conf_thres=cfg.get("infer.conf_thres", 0.45),
                iou_thres=cfg.get("infer.iou_thres", 0.45),
                class_map=cfg.get("models.yolo_class_map"))
            eng.load(cfg.get("models.yolo_onnx"))
            return [eng]

        scene = cfg.get_scene(self.scene_id)
        specs = scene.get("yolo_weights", []) or []
        scene_conf = scene.get("conf_thres", cfg.get("infer.conf_thres", 0.45))
        engines: list[YoloEngine] = []
        for spec in specs:
            path = spec.get("path")
            try:
                eng = YoloEngine(
                    conf_thres=scene_conf,
                    iou_thres=cfg.get("infer.iou_thres", 0.45),
                    class_map=spec.get("class_map"))
                eng.load(path)
                engines.append(eng)
            except Exception as e:  # noqa: BLE001 单头缺失不应拖垮整页
                log.warning(f"跳过不可用模型 {path}: {e}")
        return engines

    def _execute(self, msg: AgentMessage) -> AgentMessage:
        cfg = ConfigLoader()
        if self.yolo is not None:
            engines = [self.yolo]
        else:
            engines = self._build_engines(cfg)

        paths = msg.payload.get("image_paths", []) or []
        detections: list[dict] = []
        for eng in engines:
            for p in paths:
                try:
                    detections.extend(eng.infer(p))
                except Exception as e:  # noqa: BLE001
                    log.warning(f"推理失败 {p}: {e}")

        # 堆放物倾斜检测（Detecting-danger 独门能力，按场景开关）
        if self.scene_id:
            try:
                scene = cfg.get_scene(self.scene_id)
                lod_cfg = scene.get("load_object_detection", {}) or {}
                if lod_cfg.get("enabled"):
                    from core.load_object_detector import LoadObjectDetector
                    lod = LoadObjectDetector(lod_cfg)
                    for p in paths:
                        try:
                            detections.extend(lod.detect_and_assess(p))
                        except Exception as e:  # noqa: BLE001
                            log.warning(f"堆放物检测失败 {p}: {e}")
            except Exception as e:  # noqa: BLE001
                log.warning(f"堆放物配置读取失败: {e}")

        # 映射可读描述：项目白名单优先，否则 COCO 中文释义，再否则原名
        for d in detections:
            d["violation_desc"] = WHITELIST_CN.get(
                d["cls"], COCO_CN.get(d["cls"], d["cls"]))

        # 按类别合并重复描述，避免同一个目标 NMS 后仍有多个同类框导致列表刷屏
        seen_cls = set()
        violation_descs: list[str] = []
        for d in detections:
            if d["cls"] not in seen_cls:
                seen_cls.add(d["cls"])
                if d["cls"] not in SAFE_SIGNAL_CLASSES:
                    violation_descs.append(d["violation_desc"])

        # 模型能力提示
        fire_hit = any(d["cls"] in FIRE_CLASSES for d in detections)
        lim = None
        if not detections:
            lim = ("当前图像未达到检测置信度阈值（≥%.2f），若场景确实存在危险要素，"
                   "建议人工复核确认。" % cfg.get("infer.conf_thres", 0.45))
        elif not fire_hit and self.scene_id in (None, "hot_work"):
            lim = ("本次未检出明火/烟雾等火情目标（模型仅识别 Fire/smoke 类）；"
                   "防护面罩/灭火器/易燃物需结合规范与人工核查")

        return AgentMessage(
            task_id=msg.task_id,
            agent="vision",
            status="success",
            payload={
                "detections": detections,
                "violation_descs": violation_descs,
                "fire_model_limitation": lim,
                "input_summary": {
                    "image_paths": paths,
                    "engines": len(engines),
                },
            },
        )
