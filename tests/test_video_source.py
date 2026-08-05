"""多路视频源测试：用本地合成视频验证 VideoSource / MultiSourceMonitor。"""

import cv2
import numpy as np

from core.video_source import MultiSourceMonitor, VideoSource


def _make_video(path: str, seconds: int = 1, fps: int = 5) -> None:
    writer = cv2.VideoWriter(
        path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (64, 64))
    for _ in range(seconds * fps):
        frame = np.full((64, 64, 3), 120, dtype=np.uint8)
        writer.write(frame)
    writer.release()


def test_video_source_reads_local_file(tmp_path):
    vp = str(tmp_path / "source.mp4")
    _make_video(vp)
    with VideoSource(vp) as source:
        ok, frame = source.read()
        assert ok is True
        assert frame is not None
        assert frame.shape == (64, 64, 3)


def test_video_source_invalid_source_returns_false():
    ok, frame = VideoSource("__not_a_source__").read()
    assert ok is False
    assert frame is None


def test_multi_source_monitor_grab_all(tmp_path):
    vp1 = str(tmp_path / "a.mp4")
    vp2 = str(tmp_path / "b.mp4")
    _make_video(vp1)
    _make_video(vp2)

    def analyze(frame):
        return [{"cls": "person", "conf": 0.5}], {
            "status": "合规", "level": "safe", "color": "#43a047",
            "violations": [], "safe": [], "reasons": ["ok"],
        }

    monitor = MultiSourceMonitor([vp1, vp2])
    results = monitor.grab_all(analyze)
    assert len(results) == 2
    assert all(r["ok"] for r in results)
    assert results[0]["compliance"]["status"] == "合规"
    assert results[1]["detections"][0]["cls"] == "person"


def test_demo_source_reads_synthetic_frame():
    """demo:// 伪源：read 返回合成 BGR 帧，无需真实视频文件。"""
    ok, frame = VideoSource("demo://").read()
    assert ok is True
    assert frame is not None
    assert frame.ndim == 3
    assert frame.shape[2] == 3  # HxWx3 BGR


def test_check_source_demo_ok():
    """check_source 对 demo:// 始终可达，返回有效分辨率。"""
    from core.video_source import check_source
    r = check_source("demo://")
    assert r["ok"] is True
    assert r["width"] > 0
    assert r["height"] > 0