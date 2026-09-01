"""融合定级段（M05）：消费视觉检测结果 + 规范合规结论，定级风险并过滤误报。

逻辑（LLD §3.1~3.3）：
- 遍历 detections，按 (detect类别, compliance结论) 查矩阵取最高风险等级；
- spark 且 conf < 阈值 → 入 filtered_fp（误报过滤，SRS 3.5）；
- 输出 risk_level / filtered_fp / reasons。
"""
from __future__ import annotations

import os

import yaml

from pipeline.base import StageBase, StageMessage
from core.config import ConfigLoader
from core.false_positive import filter_ppe_contradiction, filter_smoke_vest_conflict
from core.yolo_engine import WHITELIST
from core.logging import get_logger
log = get_logger(__name__)

# 风险等级序：用于取"最高"风险
RISK_ORDER = {"低": 0, "一般": 1, "较大": 2, "重大": 3}

_DEFAULT_RULES = "config/rules/hot_work.yaml"


class FusionStage(StageBase):
    """风险融合定级段。"""

    def __init__(self, rules_path: str | None = None, scene_id: str | None = None):
        if scene_id:
            cfg = ConfigLoader()
            rules_path = cfg.get_scene(scene_id).get("risk_matrix", _DEFAULT_RULES)
        self._rules_path = rules_path or _DEFAULT_RULES
        self._matrix: list[dict] = []
        self._fp_filter: dict = {}
        self._load(self._rules_path)

    def _load(self, path: str) -> None:
        if not os.path.exists(path):
            log.warning(f"规则矩阵缺失: {path}，使用空矩阵")
            self._matrix, self._fp_filter = [], {}
            return
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        self._matrix = data.get("matrix", [])
        self._fp_filter = data.get("fp_filter", {})

    def _lookup(self, cls: str, verdicts: set[str]) -> tuple[str | None, str]:
        """查矩阵：类别匹配且(compliance=任何 或 命中 verdicts)的首条。"""
        for row in self._matrix:
            if row.get("detect") != cls:
                continue
            comp = row.get("compliance", "")
            if comp == "任何" or comp in verdicts:
                return row.get("risk"), row.get("reason", "")
        return None, ""

    def _execute(self, msg: StageMessage) -> StageMessage:
        detections = msg.payload.get("detections", []) or []
        compliance = msg.payload.get("compliance", []) or []
        verdicts = {c.get("verdict", "") for c in compliance if c.get("verdict")}
        spark_conf_min = float(self._fp_filter.get("spark_conf_min", 0.55))

        filtered_fp: list[dict] = []
        matched_risks: list[str] = []
        reasons: list[str] = []

        detections, conflict_fp = filter_smoke_vest_conflict(detections)
        filtered_fp.extend(conflict_fp)
        detections, ppe_fp = filter_ppe_contradiction(detections)
        filtered_fp.extend(ppe_fp)

        for det in detections:
            cls = det.get("cls")
            if cls not in WHITELIST and cls is not None:
                # 白名单外目标不纳入（C4 隐私合规）
                continue
            conf = float(det.get("conf", 1.0))

            # 误报过滤：spark 低置信判为光斑
            if cls == "spark" and conf < spark_conf_min:
                filtered_fp.append(det)
                continue

            risk, reason = self._lookup(cls, verdicts)
            if risk:
                matched_risks.append(risk)
                if reason:
                    reasons.append(reason)

        if matched_risks:
            risk_level = max(matched_risks, key=lambda r: RISK_ORDER.get(r, 0))
        else:
            # 无视觉命中时，检查作业票/规范侧是否已有不
            # 合规字段 — 避免 Fusion 把规范判定的“作业
            # 票不合规”降级为“低风险”（SRS 3.2.3 补丁）
            non_compliant_fields = [
                c.get("label", "") for c in compliance
                if c.get("verdict") == "不合规"
            ]
            if non_compliant_fields:
                risk_level = "一般"
                reasons.append(
                    f"作业票字段不合规（{'; '.join(non_compliant_fields)}），"
                    "但影像未检出违规目标，请人工复核"
                )
            elif detections:
                risk_level = "低"
                reasons.append("检出目标均判为误报或未纳入白名单")
            else:
                risk_level = "低"
                reasons.append("未检出违规目标")

        # 去重展示：多个同类检测框可能命中同一条 reason
        unique_reasons = []
        seen = set()
        for r in reasons:
            if r not in seen:
                seen.add(r)
                unique_reasons.append(r)

        msg.status = "success"
        msg.payload = {
            "risk_level": risk_level,
            "filtered_fp": filtered_fp,
            "reasons": unique_reasons,
            "input_summary": {
                "detections": len(detections),
                "compliance": len(compliance),
            },
        }
        return msg
