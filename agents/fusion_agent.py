"""融合 Agent（M05）：消费视觉检测结果 + 规范合规结论，定级风险并过滤误报。

逻辑（LLD §3.1~3.3）：
- 遍历 detections，按 (detect类别, compliance结论) 查矩阵取最高风险等级；
- spark 且 conf < 阈值 → 入 filtered_fp（误报过滤，SRS 3.5）；
- 输出 risk_level / filtered_fp / reasons。
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

from agents.base import AgentBase, AgentMessage
from core.config import ConfigLoader
from core.yolo_engine import WHITELIST

# 风险等级序：用于取"最高"风险
RISK_ORDER = {"低": 0, "一般": 1, "较大": 2, "重大": 3}

_DEFAULT_RULES = "config/rules/hot_work.yaml"


class FusionAgent(AgentBase):
    """风险融合定级 Agent。"""

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
            print(f"[FusionAgent] 规则矩阵缺失: {path}，使用空矩阵")
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

    def _execute(self, msg: AgentMessage) -> AgentMessage:
        detections = msg.payload.get("detections", []) or []
        compliance = msg.payload.get("compliance", []) or []
        verdicts = {c.get("verdict", "") for c in compliance if c.get("verdict")}
        spark_conf_min = float(self._fp_filter.get("spark_conf_min", 0.55))

        filtered_fp: list[dict] = []
        matched_risks: list[str] = []
        reasons: list[str] = []

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
        elif detections:
            # 全部入误报或白名单外 → 无有效风险
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
        }
        return msg
