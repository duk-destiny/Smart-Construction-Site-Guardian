"""反馈样本转场景级 YOLO 数据测试。"""

from pathlib import Path

from core.feedback_dataset import (
    feedback_yolo_rows,
    scene_for_detections,
    write_feedback_dataset,
)


def _sample(task_id, sample_id, image_path, detections, corrections):
    import json
    return {
        "task_id": task_id,
        "id": sample_id,
        "status": "confirmed",
        "image_path": str(image_path),
        "detection_json": json.dumps(detections),
        "corrected_labels_json": json.dumps(corrections),
    }


def test_scene_for_detections():
    assert scene_for_detections([{"cls": "spark"}]) == "fire"
    assert scene_for_detections([{"cls": "no_helmet"}]) == "ppe"
    assert scene_for_detections([{"cls": "spark"}, {"cls": "no_helmet"}]) is None
    assert scene_for_detections([]) is None


def test_feedback_yolo_rows_maps_scene_classes():
    rows = feedback_yolo_rows(
        [
            {"cls": "helmet", "bbox": [0.5, 0.5, 0.2, 0.2]},
            {"cls": "no_helmet", "bbox": [0.5, 0.5, 0.2, 0.2]},
        ],
        [
            {"corrected_cls": "helmet"},
            {"is_fp": True},
        ],
        "ppe",
    )
    assert rows[0].startswith("0 ")


def test_write_feedback_dataset(tmp_path):
    img = tmp_path / "a.jpg"
    img.write_bytes(b"fake-jpeg")
    samples = [
        _sample(
            "t1", "f1", img,
            [{"cls": "smoke", "conf": 0.9, "bbox": [0.5, 0.5, 0.2, 0.2]}],
            [{"corrected_cls": "smoke"}],
        ),
        _sample(
            "t2", "f2", img,
            [{"cls": "no_helmet", "conf": 0.8, "bbox": [0.5, 0.5, 0.2, 0.2]}],
            [{"corrected_cls": "no_helmet"}],
        ),
    ]
    out = tmp_path / "yolo"
    summary = write_feedback_dataset(samples, out)
    assert (out / "fire" / "data.yaml").exists()
    assert (out / "ppe" / "data.yaml").exists()
    assert summary["scenes"]["fire"]["train"] + summary["scenes"]["fire"]["val"] == 1
    assert summary["scenes"]["ppe"]["train"] + summary["scenes"]["ppe"]["val"] == 1
