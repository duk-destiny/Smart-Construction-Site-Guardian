"""轻量 IoU 目标跟踪：为实时检测目标分配稳定 ID 和连续帧数。"""
from __future__ import annotations


def _iou(a: list[float], b: list[float]) -> float:
    ax1, ay1 = a[0] - a[2] / 2, a[1] - a[3] / 2
    ax2, ay2 = a[0] + a[2] / 2, a[1] + a[3] / 2
    bx1, by1 = b[0] - b[2] / 2, b[1] - b[3] / 2
    bx2, by2 = b[0] + b[2] / 2, b[1] + b[3] / 2
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    union = ((ax2 - ax1) * (ay2 - ay1) +
             (bx2 - bx1) * (by2 - by1) - inter)
    return inter / max(union, 1e-9)


class IoUTracker:
    """按类别 + IoU 匹配的轻量跟踪器。"""

    def __init__(self, iou_threshold: float = 0.30,
                 max_lost: int = 10) -> None:
        self.iou_threshold = iou_threshold
        self.max_lost = max_lost
        self._tracks: dict[int, dict] = {}
        self._next_id = 1

    def update(self, detections: list[dict]) -> list[dict]:
        """更新轨迹，给检测项附加 track_id / track_frames。"""
        matched_ids: set[int] = set()
        for det in detections:
            bbox = det.get("bbox")
            if not isinstance(bbox, list) or len(bbox) != 4:
                continue
            best_id: int | None = None
            best_iou = self.iou_threshold
            for tid, track in self._tracks.items():
                if track["cls"] != det.get("cls"):
                    continue
                iou = _iou(bbox, track["bbox"])
                if iou > best_iou:
                    best_id, best_iou = tid, iou
            if best_id is not None:
                track = self._tracks[best_id]
                track["bbox"] = bbox
                track["frames"] += 1
                track["lost"] = 0
                det["track_id"] = best_id
                det["track_frames"] = track["frames"]
                matched_ids.add(best_id)
            else:
                tid = self._next_id
                self._next_id += 1
                self._tracks[tid] = {
                    "cls": det.get("cls"),
                    "bbox": bbox,
                    "frames": 1,
                    "lost": 0,
                }
                det["track_id"] = tid
                det["track_frames"] = 1
                matched_ids.add(tid)

        for tid, track in list(self._tracks.items()):
            if tid not in matched_ids:
                track["lost"] += 1
                if track["lost"] > self.max_lost:
                    del self._tracks[tid]
        return detections

    def reset(self) -> None:
        self._tracks.clear()
        self._next_id = 1

    @property
    def track_count(self) -> int:
        return len(self._tracks)
