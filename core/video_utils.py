"""视频抽帧：OpenCV 按目标 fps 抽帧写临时图片，超出 max_sec 截断。

用于把上传视频转为逐帧图片交给视觉 Agent 检测（LLD §2 视觉链路）。
仅本地处理，不上传任何帧（C1/C4）。
"""
from __future__ import annotations

import os
import tempfile

import cv2


class VideoUtils:
    """视频抽帧工具（无状态）。"""

    @staticmethod
    def extract_frames(path: str, fps: int = 1, max_sec: int = 10) -> list[str]:
        """抽取视频帧为临时 JPG，返回帧路径列表。

        Args:
            path: 视频文件路径。
            fps: 目标抽帧频率（帧/秒）。
            max_sec: 最多抽取时长（秒），超出截断。
        Returns:
            帧图片路径列表；文件不可读时返回空列表（不抛异常）。
        """
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            return []
        video_fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        # 源帧率下每隔多少帧抽一帧；源帧率为 0 时退化为逐帧
        frame_interval = max(1, int(round(video_fps / fps))) if video_fps else 1
        max_frames = max(1, int(max_sec * fps))

        out: list[str] = []
        idx = 0
        while len(out) < max_frames:
            ret, frame = cap.read()
            if not ret:
                break
            if idx % frame_interval == 0:
                fd, fp = tempfile.mkstemp(suffix=".jpg", prefix="frame_")
                os.close(fd)
                cv2.imwrite(fp, frame)
                out.append(fp)
            idx += 1
        cap.release()
        return out
