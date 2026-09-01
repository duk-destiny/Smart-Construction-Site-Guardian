"""认知层（真 Agent 内核）包：Plan-and-Execute 受限范式。

模块分工：
- models:    Step / Plan / StepResult / RunContext 数据契约（pydantic）
- tools:     ToolSpec / ToolCtx / TOOL_REGISTRY（工具层，统一接口）
- kernel:    PlanExecutor（规划 → 逐步执行 → 摘要 → 汇总，双闸收敛）
- run_service: PlanRunService（挂起-恢复状态机 + 线程池 + 孤儿扫描）
- playbooks: 剧本注册骨架（意图 → system prompt 模板 + 规则模板档兜底）

铁律（设计文档 §5.8）：
- LLM 永不进入风险定级路径（定级保持 compliance.severity 查表）；
- 副作用工具由代码按 ToolSpec.side_effect 强制挂起人工确认，不信 LLM 自报；
- 会话记忆只存摘要，digest 由代码拼接（≤300 字），零 LLM。
"""
from __future__ import annotations

__all__ = [
    "models",
    "tools",
    "kernel",
    "run_service",
    "playbooks",
]
