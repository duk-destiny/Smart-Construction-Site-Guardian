"""多路视频源读取：支持 RTSP/HTTP 摄像头流与本地视频文件，以及 "demo://" 合成源。

与上传研判的视频抽帧不同，这里面向实时监测页的多摄像头按帧抓取，
每次读取一帧并交给 RealtimeEngine 做轻量检测。
"demo://" 为纯 numpy 合成帧源，零依赖、确定性，用于无真实视频源时的接入自检。
"""
from __future__ import annotations

import threading
from typing import Callable, Iterable

import cv2
import numpy as np

_DEMO_TOKEN = "demo://"
_DEMO_H, _DEMO_W = 480, 640


class VideoSource:
    """单个 RTSP/文件/demo 视频源，按需打开并读取最新帧。"""

    def __init__(self, source: str) -> None:
        self.source = (source or "").strip()
        self._demo = self.source.startswith(_DEMO_TOKEN)
        self._cap: cv2.VideoCapture | None = None
        self._frame_idx = 0

    def read(self) -> tuple[bool, np.ndarray | None]:
        """返回 (是否成功, BGR 帧)；失败时自动释放连接。"""
        if self._demo:
            return True, self._demo_frame()
        if self._cap is None or not self._cap.isOpened():
            self._open()
        if self._cap is None or not self._cap.isOpened():
            return False, None
        try:
            ok, frame = self._cap.read()
        except Exception:  # noqa: BLE001 网络源异常按不可读处理
            self.release()
            return False, None
        if not ok:
            self.release()
            return False, None
        return True, frame

    def _demo_frame(self) -> np.ndarray:
        """合成一帧 BGR 图像：渐变背景 + 移动高亮块，确定性可复现。"""
        self._frame_idx += 1
        frame = np.full((_DEMO_H, _DEMO_W, 3), 30, dtype=np.uint8)
        for x in range(0, _DEMO_W, 8):
            col = (x * 255 // _DEMO_W) % 256
            frame[:, x:x + 8, 0] = col
            frame[:, x:x + 8, 1] = col // 2
        bx = 40 + (self._frame_idx * 5) % (_DEMO_W - 120)
        by = 60 + (self._frame_idx * 3) % (_DEMO_H - 120)
        frame[by:by + 80, bx:bx + 80] = (0, 120, 255)  # 暖色高亮块（BGR 橙）
        cv2.putText(frame, f"demo frame {self._frame_idx}", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        return frame

    def _open(self) -> None:
        try:
            self._cap = cv2.VideoCapture(self.source)
            if self._cap is not None:
                self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:  # noqa: BLE001
            self._cap = None

    def release(self) -> None:
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:  # noqa: BLE001
                pass
            self._cap = None

    def __enter__(self) -> "VideoSource":
        return self

    def __exit__(self, *exc) -> None:
        self.release()


class MultiSourceMonitor:
    """按索引抓取多路视频源，供实时页批量展示。"""

    def __init__(self, sources: Iterable[str]) -> None:
        self.sources = [VideoSource(s) for s in sources if s and s.strip()]

    def grab_all(
        self,
        analyze: Callable[[np.ndarray], tuple[list[dict], dict]],
        draw: Callable[[np.ndarray, dict], np.ndarray] | None = None,
    ) -> list[dict]:
        """依次读取每路源并返回结果列表。"""
        results: list[dict] = []
        for idx, source in enumerate(self.sources):
            ok, frame = source.read()
            entry: dict = {
                "index": idx,
                "source": source.source,
                "ok": ok,
            }
            if ok and frame is not None:
                dets, comp = analyze(frame)
                entry["detections"] = dets
                entry["compliance"] = comp
                if draw is not None:
                    entry["annotated"] = draw(frame, comp)
            results.append(entry)
        return results


def check_source(source: str, timeout: float = 5.0) -> dict:
    """对单个视频源做连通性自检：打开 -> 读 1 帧 -> 取分辨率/fps -> 释放。

    返回 {ok, width, height, fps, error}。读帧在子线程中执行，超时即判失败，
    避免死掉的 RTSP 源卡住自检。demo:// 视为始终可达。
    """
    result = {"ok": False, "width": 0, "height": 0, "fps": 0.0, "error": ""}
    src = (source or "").strip()
    if not src:
        result["error"] = "空源"
        return result
    vs = VideoSource(src)
    box: dict = {}

    def _work() -> None:
        ok, frame = vs.read()
        box["ok"] = ok
        box["frame"] = frame
        if ok and vs._cap is not None:
            try:
                box["fps"] = float(vs._cap.get(cv2.CAP_PROP_FPS) or 0.0)
            except Exception:  # noqa: BLE001
                box["fps"] = 0.0

    t = threading.Thread(target=_work, daemon=True)
    t.start()
    t.join(timeout)
    vs.release()
    if t.is_alive():
        result["error"] = f"读取超时（>{timeout:.0f}s）"
        return result
    if not box.get("ok") or box.get("frame") is None:
        result["error"] = "无法读取帧"
        return result
    frame = box["frame"]
    result["ok"] = True
    result["height"], result["width"] = frame.shape[:2]
    result["fps"] = box.get("fps", 30.0)  # demo 源默认 30fps
    return result