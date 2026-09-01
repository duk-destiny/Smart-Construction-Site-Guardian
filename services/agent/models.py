"""认知层数据契约（设计文档 §5.3.1 / §5.5）：全 pydantic，结构化通信。

字段与认知层四表（chat_sessions / chat_messages / agent_chat_runs /
agent_chat_run_steps）对齐；计划与步骤经 JSON Schema 约束 + 本模型二次校验。
契约化通信原则：认知层与工具层之间只传结构化 JSON，不传自然语言。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# run 级七态状态机（与 agent_chat_runs.status CHECK 一致）
RunStatus = Literal[
    "pending", "running", "pending_confirm",
    "completed", "degraded", "failed", "cancelled",
]
# 步骤级三态 + 待执行
StepStatus = Literal["pending", "success", "degraded", "failed"]

# 计划硬上限：≤8 步防无界循环（§5.3.1）；本地降级档收紧 ≤4（§7）
MAX_PLAN_STEPS = 8
LOCAL_PLAN_STEPS = 4


class Step(BaseModel):
    """计划中的一步：工具名必须命中注册表白名单，参数按工具 args_schema 二次校验。"""

    tool: str = Field(min_length=1, max_length=64)
    args: dict = Field(default_factory=dict)
    reason: str = Field(default="", max_length=60)   # 一句话理由，前端展示用


class Plan(BaseModel):
    """LLM 规划产物（LLM 出场①）。

    need_confirm 不由 LLM 填写生效——由代码按工具 side_effect 强制，
    反序列化后即使 LLM 自报 False 也会被内核改写（§5.8）。
    """

    goal: str = Field(default="", max_length=200)
    steps: list[Step] = Field(default_factory=list, max_length=MAX_PLAN_STEPS)
    need_confirm: bool = False
    max_deadline_sec: float = Field(default=30.0, ge=1.0, le=600.0)


class StepResult(BaseModel):
    """单步执行结果（落 agent_chat_run_steps；失败也留痕）。"""

    step_idx: int = -1
    tool: str = ""
    args: dict = Field(default_factory=dict)
    status: StepStatus = "pending"
    digest: str | None = None     # 代码生成的结果摘要（不存原始输出）
    error: str | None = None
    cost_ms: int = 0


class RunContext(BaseModel):
    """RunContext 持久化模型（§5.5），字段与 agent_chat_runs 对齐。

    deadline 为 monotonic 计时基准（秒），由执行器在 run/resume 时重算——
    副作用确认挂起期间不占预算（§7），恢复时重置。
    """

    run_id: str
    session_id: str
    user_id: str
    role: str = ""                    # 代码注入的工具作用域（不经 LLM）
    intent: str | None = None
    user_input: str
    plan: Plan | None = None
    status: RunStatus = "pending"
    current_step_idx: int = -1        # 最后完成的步骤索引，-1=未开始
    need_confirm: bool = False
    confirm_payload: dict | None = None
    deadline_sec: float = 30.0        # run 级墙钟总预算
    deadline: float = 0.0             # monotonic 截止时间戳（执行器内计算）
    steps: list[StepResult] = Field(default_factory=list)
    history_digests: list[str] = Field(default_factory=list)  # 历史轮摘要（§5.7）
    attachments: list[str] = Field(default_factory=list)  # 服务端校验过的对话附件路径
    recent_turns: list[dict] = Field(default_factory=list)  # 本会话最近轮原文（user/assistant，截断）
    memories: list[str] = Field(default_factory=list)  # 跨会话记忆要点（其他会话 digest）
