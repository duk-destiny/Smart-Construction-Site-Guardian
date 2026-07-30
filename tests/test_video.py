"""Task 6：视频抽帧测试（用 cv2 合成视频，无需外部素材）。"""
import os

import cv2
import numpy as np

from core.video_utils import VideoUtils


def _make_video(path: str, seconds: int = 2, fps: int = 5) -> None:
    w = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (64, 64))
    for _ in range(seconds * fps):
        frame = np.random.randint(0, 255, (64, 64, 3), dtype=np.uint8)
        w.write(frame)
    w.release()


def test_extract_one_per_second(tmp_path):
    vp = str(tmp_path / "s.mp4")
    _make_video(vp, seconds=3, fps=5)
    frames = VideoUtils.extract_frames(vp, fps=1, max_sec=10)
    # 3 秒、1fps => ~3 帧
    assert 2 <= len(frames) <= 4
    assert all(f.endswith(".jpg") for f in frames)


def test_extract_capped_by_max_sec(tmp_path):
    vp = str(tmp_path / "l.mp4")
    _make_video(vp, seconds=60, fps=5)
    frames = VideoUtils.extract_frames(vp, fps=1, max_sec=10)
    assert len(frames) <= 10


def test_extract_invalid_path_returns_empty():
    assert VideoUtils.extract_frames("nonexistent.mp4") == []


def test_extract_increases_fps(tmp_path):
    vp = str(tmp_path / "h.mp4")
    _make_video(vp, seconds=2, fps=10)
    frames_1 = VideoUtils.extract_frames(vp, fps=1, max_sec=10)
    frames_5 = VideoUtils.extract_frames(vp, fps=5, max_sec=10)
    assert len(frames_5) > len(frames_1)
