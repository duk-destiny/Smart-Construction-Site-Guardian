"""检测结果缓存（§5.10 DetectionCache）：进程内、不落盘、多轮追问提速。

约束（设计文档 §5.10 共享链路兼容约束）：
- 严格进程内（Streamlit + FastAPI 双进程部署各自独立），不落盘、不跨进程；
- key=文件内容 sha256 前 16 位——重新上传即新 key，天然隔离（§风险表）；
- TTL（默认 300s）+ 容量上限（默认 64）LRU 式逐出；
- 线程安全（内部锁）；
- 对上传研判主链路**默认关闭**（VisionStage cache=None），
  仅视频对话场景由 VideoAnalysisShell 显式 opt-in。
"""
from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict

_DEFAULT_TTL_SEC = 300.0
_DEFAULT_CAPACITY = 64


class DetectionCache:
    """进程内检测结果缓存：TTL 过期 + 容量上限（LRU 式逐出最久未访问）。"""

    def __init__(self, ttl_sec: float = _DEFAULT_TTL_SEC,
                 capacity: int = _DEFAULT_CAPACITY) -> None:
        self._ttl = max(0.0, float(ttl_sec))
        self._capacity = max(1, int(capacity))
        self._lock = threading.Lock()
        # key -> (value, expires_at)；OrderedDict 维护访问序（末尾=最新）
        self._store: OrderedDict[str, tuple[object, float]] = OrderedDict()

    @staticmethod
    def key_of_file(path: str) -> str | None:
        """文件内容 sha256 前 16 位；文件不存在/不可读返回 None（不缓存）。"""
        try:
            h = hashlib.sha256()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
            return h.hexdigest()[:16]
        except OSError:
            return None

    def get(self, key: str) -> object | None:
        """命中则返回值并刷新 LRU 位置；过期/未命中返回 None。"""
        now = time.monotonic()
        with self._lock:
            item = self._store.get(key)
            if item is None:
                return None
            value, expires_at = item
            if expires_at <= now:
                self._store.pop(key, None)
                return None
            self._store.move_to_end(key)
            return value

    def put(self, key: str, value: object) -> None:
        """写入并设置过期时间；超过容量上限逐出最久未访问项。"""
        if not key:
            return
        now = time.monotonic()
        with self._lock:
            self._store[key] = (value, now + self._ttl)
            self._store.move_to_end(key)
            while len(self._store) > self._capacity:
                self._store.popitem(last=False)

    def purge_expired(self) -> int:
        """主动清理过期项，返回清理条数（供测试/诊断）。"""
        now = time.monotonic()
        removed = 0
        with self._lock:
            for k in [k for k, (_, exp) in self._store.items() if exp <= now]:
                self._store.pop(k, None)
                removed += 1
        return removed

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
