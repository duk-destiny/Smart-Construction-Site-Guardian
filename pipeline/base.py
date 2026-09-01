"""流水线段基础契约：StageMessage 与 StageBase（代码规范 §4）。

所有流水线段继承 StageBase，实现 _execute(msg)->StageMessage；
基类统一负责计时与"异常转 failed"（防止任意段崩溃拖垮主链路，SRS 3.2.4）。
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class StageMessage:
    """流水线段间通信的标准信封（强制封装，禁止裸 dict/list 跨段传递）。

    status: pending | success | failed | degraded
    """

    task_id: str
    agent: str
    status: str
    payload: dict = field(default_factory=dict)
    error: str | None = None
    cost_ms: int = 0

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "agent": self.agent,
            "status": self.status,
            "payload": self.payload,
            "error": self.error,
            "cost_ms": self.cost_ms,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "StageMessage":
        return cls(
            task_id=d["task_id"],
            agent=d["agent"],
            status=d["status"],
            payload=d.get("payload", {}),
            error=d.get("error"),
            cost_ms=d.get("cost_ms", 0),
        )


class StageBase(ABC):
    """流水线段基类。子类只实现 _execute，计时与异常兜底由基类完成。"""

    def run(self, msg: StageMessage) -> StageMessage:
        """执行并计时；任何未捕获异常转为 status=failed（不向上抛）。"""
        start = time.perf_counter()
        try:
            out = self._execute(msg)
        except Exception as e:  # noqa: BLE001 - 顶层兜底，保证进程不退出
            out = StageMessage(
                task_id=msg.task_id,
                agent=msg.agent,
                status="failed",
                payload={},
                error=f"{type(e).__name__}: {e}",
            )
        out.cost_ms = int((time.perf_counter() - start) * 1000)
        return out

    @abstractmethod
    def _execute(self, msg: StageMessage) -> StageMessage:
        """子类实现的核心逻辑（不含计时与异常捕获，由基类包裹）。"""
        ...
