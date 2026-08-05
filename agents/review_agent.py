"""复核 Agent：对高风险或证据不足的结果标记人工复核。

它不是直接改判，而是把“需要人工确认”的结论显式交给安全员，
与人工纠偏闭环共同构成 AI 纠偏管理能力。
"""
from __future__ import annotations

from agents.base import AgentBase, AgentMessage

# 高风险类别在置信度低于该值时进入复核
REVIEW_CONF_THRESHOLD = 0.55
HIGH_RISK_CLASSES = {
    "spark", "smoke", "no_helmet", "no_vest",
    "face_shield", "extinguisher", "load_object_tilted",
}
HIGH_RISK_LEVELS = {"较大", "重大"}


class ReviewAgent(AgentBase):
    """复核 Agent：消费融合结果，输出是否需要人工复核。"""

    def _execute(self, msg: AgentMessage) -> AgentMessage:
        detections = msg.payload.get("detections", []) or []
        compliance = msg.payload.get("compliance", []) or []
        risk_level = msg.payload.get("risk_level", "低")
        reasons: list[str] = []

        if risk_level in HIGH_RISK_LEVELS:
            for det in detections:
                cls = det.get("cls")
                conf = float(det.get("conf", 1.0) or 1.0)
                if cls in HIGH_RISK_CLASSES and conf < REVIEW_CONF_THRESHOLD:
                    reasons.append(
                        f"{cls} 置信度 {conf:.2f} 低于 {REVIEW_CONF_THRESHOLD:.2f}，"
                        "属高风险项，建议人工复核")
            for item in compliance:
                if item.get("needs_review"):
                    reasons.append(
                        f"规范条款未明确匹配：{item.get('label', '')}，建议人工补充依据")

        msg.status = "success"
        msg.payload = {
            "needs_review": bool(reasons),
            "review_reasons": reasons,
            "input_summary": {
                "risk_level": risk_level,
                "detections": len(detections),
                "compliance": len(compliance),
            },
        }
        return msg
