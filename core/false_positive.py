"""跨场景误报过滤：低置信度烟雾/火花与人员/防护装备重叠时降级。"""
from __future__ import annotations

SMOKE_LIKE_CLASSES = {"smoke", "spark"}
PROTECTIVE_CLASSES = {"person", "vest", "no_vest", "helmet", "no_helmet"}
LOW_CONF_THRESHOLD = 0.45
IOU_THRESHOLD = 0.2
PPE_POSITIVE_BY_NEGATIVE = {"no_helmet": "helmet", "no_vest": "vest"}


def _iou(a: dict, b: dict) -> float:
    ax1, ay1 = a["bbox"][0] - a["bbox"][2] / 2, a["bbox"][1] - a["bbox"][3] / 2
    ax2, ay2 = a["bbox"][0] + a["bbox"][2] / 2, a["bbox"][1] + a["bbox"][3] / 2
    bx1, by1 = b["bbox"][0] - b["bbox"][2] / 2, b["bbox"][1] - b["bbox"][3] / 2
    bx2, by2 = b["bbox"][0] + b["bbox"][2] / 2, b["bbox"][1] + b["bbox"][3] / 2
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    union = ((ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter)
    return inter / max(union, 1e-9)


def _center_in(a: dict, b: dict) -> bool:
    cx, cy = a["bbox"][0], a["bbox"][1]
    x1, y1 = b["bbox"][0] - b["bbox"][2] / 2, b["bbox"][1] - b["bbox"][3] / 2
    x2, y2 = b["bbox"][0] + b["bbox"][2] / 2, b["bbox"][1] + b["bbox"][3] / 2
    return x1 <= cx <= x2 and y1 <= cy <= y2


def filter_smoke_vest_conflict(
    detections: list[dict],
    low_conf: float = LOW_CONF_THRESHOLD,
    iou_threshold: float = IOU_THRESHOLD,
) -> tuple[list[dict], list[dict]]:
    """过滤与人员/防护装备重叠的低置信度烟雾/火花，返回 (保留, 误报)。"""
    protective = [
        d for d in detections
        if d.get("cls") in PROTECTIVE_CLASSES and len(d.get("bbox", [])) == 4
    ]
    kept: list[dict] = []
    filtered: list[dict] = []
    for det in detections:
        if det.get("cls") in SMOKE_LIKE_CLASSES and \
                float(det.get("conf", 1.0) or 1.0) < low_conf and \
                len(det.get("bbox", [])) == 4:
            if any(
                _iou(det, p) > iou_threshold or _center_in(det, p)
                for p in protective
            ):
                item = dict(det)
                item["filtered_reason"] = "与人员/防护装备重叠，低置信烟雾疑似误报"
                filtered.append(item)
                continue
        kept.append(det)
    return kept, filtered


def filter_ppe_contradiction(
    detections: list[dict],
    iou_threshold: float = IOU_THRESHOLD,
    conf_margin: float = 0.15,
) -> tuple[list[dict], list[dict]]:
    """过滤同一 PPE 类型的矛盾框：正向与负向重叠时优先保留高置信正向。

    模型可能同时输出 helmet/no_helmet 或 vest/no_vest，尤其低置信负向框
    常由误检产生；若正向框置信度足够，将重叠负向框判为误报，避免合规
    画面因矛盾框被误判为不合规。
    """
    kept: list[dict] = list(detections)
    filtered: list[dict] = []
    for negative_cls, positive_cls in PPE_POSITIVE_BY_NEGATIVE.items():
        negatives = [
            d for d in kept
            if d.get("cls") == negative_cls and len(d.get("bbox", [])) == 4
        ]
        positives = [
            d for d in kept
            if d.get("cls") == positive_cls and len(d.get("bbox", [])) == 4
        ]
        if not negatives or not positives:
            continue
        drop: set[int] = set()
        for neg in negatives:
            for pos in positives:
                if not (
                    _iou(neg, pos) > iou_threshold
                    or _center_in(neg, pos)
                    or _center_in(pos, neg)
                ):
                    continue
                pos_conf = float(pos.get("conf", 0.0) or 0.0)
                neg_conf = float(neg.get("conf", 0.0) or 0.0)
                if pos_conf >= max(0.40, neg_conf - conf_margin):
                    item = dict(neg)
                    item["filtered_reason"] = (
                        f"与{positive_cls}检测框重叠，{negative_cls}疑似矛盾误报"
                    )
                    filtered.append(item)
                    drop.add(id(neg))
                    break
        if drop:
            kept = [d for d in kept if id(d) not in drop]
    return kept, filtered
