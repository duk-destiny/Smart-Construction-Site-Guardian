"""跨场景误报过滤测试：低置信度烟雾与防护装备重叠时过滤。"""

from core.false_positive import filter_ppe_contradiction, filter_smoke_vest_conflict


def _det(cls, conf, bbox):
    return {"cls": cls, "conf": conf, "bbox": bbox}


def test_low_conf_smoke_overlapping_vest_filtered():
    dets = [
        _det("smoke", 0.30, [0.5, 0.5, 0.3, 0.3]),
        _det("vest", 0.60, [0.5, 0.5, 0.4, 0.4]),
    ]
    kept, filtered = filter_smoke_vest_conflict(dets)
    assert len(kept) == 1
    assert kept[0]["cls"] == "vest"
    assert len(filtered) == 1
    assert "filtered_reason" in filtered[0]


def test_high_conf_smoke_kept_even_with_vest():
    dets = [
        _det("smoke", 0.85, [0.5, 0.5, 0.3, 0.3]),
        _det("vest", 0.60, [0.5, 0.5, 0.4, 0.4]),
    ]
    kept, filtered = filter_smoke_vest_conflict(dets)
    assert len(kept) == 2
    assert filtered == []


def test_smoke_center_inside_vest_filtered():
    dets = [
        _det("smoke", 0.40, [0.5, 0.5, 0.1, 0.1]),
        _det("vest", 0.60, [0.5, 0.5, 0.6, 0.6]),
    ]
    kept, filtered = filter_smoke_vest_conflict(dets)
    assert len(kept) == 1
    assert kept[0]["cls"] == "vest"
    assert filtered[0]["cls"] == "smoke"


def test_ppe_contradiction_keeps_positive_overlapping():
    dets = [
        _det("no_helmet", 0.55, [0.5, 0.5, 0.4, 0.4]),
        _det("helmet", 0.72, [0.5, 0.5, 0.3, 0.3]),
    ]
    kept, filtered = filter_ppe_contradiction(dets)
    assert [d["cls"] for d in kept] == ["helmet"]
    assert filtered[0]["cls"] == "no_helmet"
    assert "filtered_reason" in filtered[0]


def test_ppe_contradiction_keeps_strong_negative():
    dets = [
        _det("no_vest", 0.90, [0.5, 0.5, 0.4, 0.4]),
        _det("vest", 0.55, [0.5, 0.5, 0.3, 0.3]),
    ]
    kept, filtered = filter_ppe_contradiction(dets)
    assert len(kept) == 2
    assert filtered == []
