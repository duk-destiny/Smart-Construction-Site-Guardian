"""Agent 基础契约：AgentMessage 与 AgentBase（代码规范 §4）。

所有业务 Agent 继承 AgentBase，实现 _execute(msg)->AgentMessage；
基类统一负责计时与"异常转 failed"（防止任意 Agent 崩溃拖垮主链路，SRS 3.2.4）。
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class AgentMessage:
    """Agent 间通信的标准信封（强制封装，禁止裸 dict/list 跨 Agent）。

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
    def from_dict(cls, d: dict) -> "AgentMessage":
        return cls(
            task_id=d["task_id"],
            agent=d["agent"],
            status=d["status"],
            payload=d.get("payload", {}),
            error=d.get("error"),
            cost_ms=d.get("cost_ms", 0),
        )


class AgentBase(ABC):
    """业务 Agent 基类。子类只实现 _execute，计时与异常兜底由基类完成。"""

    def run(self, msg: AgentMessage) -> AgentMessage:
        """执行并计时；任何未捕获异常转为 status=failed（不向上抛）。"""
        start = time.perf_counter()
        try:
            out = self._execute(msg)
        except Exception as e:  # noqa: BLE001 - 顶层兜底，保证进程不退出
            out = AgentMessage(
                task_id=msg.task_id,
                agent=msg.agent,
                status="failed",
                payload={},
                error=f"{type(e).__name__}: {e}",
            )
        out.cost_ms = int((time.perf_counter() - start) * 1000)
        return out

    @abstractmethod
    def _execute(self, msg: AgentMessage) -> AgentMessage:
        """子类实现的核心逻辑（不含计时与异常捕获，由基类包裹）。"""
        ...
