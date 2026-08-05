"""IoU 目标跟踪测试。"""

from core.tracker import IoUTracker


def _det(cls, bbox):
    return {"cls": cls, "conf": 0.9, "bbox": bbox}


def test_tracker_keeps_id_for_same_object():
    tracker = IoUTracker()
    d1 = tracker.update([_det("person", [0.5, 0.5, 0.2, 0.2])])[0]
    d2 = tracker.update([_det("person", [0.51, 0.5, 0.2, 0.2])])[0]
    assert d1["track_id"] == d2["track_id"]
    assert d2["track_frames"] == 2


def test_tracker_new_class_gets_new_id():
    tracker = IoUTracker()
    a = tracker.update([_det("person", [0.5, 0.5, 0.2, 0.2])])[0]
    b = tracker.update([_det("no_helmet", [0.5, 0.5, 0.2, 0.2])])[0]
    assert a["track_id"] != b["track_id"]


def test_tracker_removes_lost_after_max_lost():
    tracker = IoUTracker(max_lost=2)
    first = tracker.update([_det("person", [0.5, 0.5, 0.2, 0.2])])[0]
    for _ in range(3):
        tracker.update([])
    again = tracker.update([_det("person", [0.5, 0.5, 0.2, 0.2])])[0]
    assert again["track_id"] != first["track_id"]
    assert again["track_frames"] == 1
