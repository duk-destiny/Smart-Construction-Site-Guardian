"""请求体模型（Pydantic）：入参校验收口；响应用服务层返回的 dict 原样整形。

枚举取值与既有 UI 口径一致：告警状态 new/confirmed/false_alarm/resolved
（ui/page_admin.py），反馈审核 pending/confirmed/rejected，风险等级
重大/较大/一般/低（services.dispatch_service.RISK_DEADLINE_HOURS）。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class ChangePasswordIn(BaseModel):
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class TextHazardIn(BaseModel):
    description: str = Field(min_length=1, max_length=2000)
    hazard_key: str = Field(min_length=1, max_length=64)
    scene_id: str = "hot_work"
    location: str | None = Field(default=None, max_length=200)


class RunIn(BaseModel):
    """发起后台研判：images 为入库相对路径列表（如 /api/tasks/media 返回值）。"""

    images: list[str] = Field(default_factory=list)
    permit_info: dict = Field(default_factory=dict)
    scene_id: str = "hot_work"


class EnhanceIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class ChatQueryIn(BaseModel):
    text: str = Field(default="", max_length=500)


class AgentChatIn(BaseModel):
    """认知层对话入口（§5.12）：session_id 可空=新建会话。

    text 允许空串：空文本=最新待办清单契约（§5.11）在快路径侧保留，
    与旧 /tasks/query-chat 行为一致（dispatch_chat 内分流）。
    attachments 为 /api/agent/uploads 返回的相对路径（服务端校验后
    强制绑定给 run_video_pipeline，不经 LLM 之手）。
    """

    text: str = Field(default="", max_length=2000)
    session_id: str | None = Field(default=None, max_length=64)
    attachments: list[str] = Field(default_factory=list, max_length=4)


class SessionCreateIn(BaseModel):
    """新建空会话（对话窗口「新建对话」按钮）。"""

    title: str | None = Field(default=None, max_length=64)


class SessionPatchIn(BaseModel):
    """会话改名/归档（对话窗口侧栏管理）。至少提供一个字段。"""

    title: str | None = Field(default=None, max_length=64)
    archived: bool | None = None


class OrderAskIn(BaseModel):
    """工单 AI 弹窗问询（责任人/管理员读单助手）。"""

    question: str = Field(min_length=1, max_length=500)


class AgentConfirmIn(BaseModel):
    """挂起确认（§5.6.2）：confirm 可携带 modified_plan（删步/改参数）。"""

    action: Literal["confirm", "cancel"]
    modified_plan: dict | None = None


class OverrideIn(BaseModel):
    new_level: Literal["重大", "较大", "一般", "低"]
    reason: str = Field(min_length=1, max_length=500)


class AlarmStatusIn(BaseModel):
    status: Literal["new", "confirmed", "false_alarm", "resolved"]


class DispatchIn(BaseModel):
    assignee: str = Field(min_length=1, max_length=64)
    hours: float = Field(gt=0, le=24 * 30)
    scene_id: str | None = None


class ReviewIn(BaseModel):
    approve: bool
    reason: str = Field(default="", max_length=500)


class WeeklyReportIn(BaseModel):
    start: str = Field(min_length=10, max_length=10,
                       description="ISO 日期 YYYY-MM-DD")
    end: str = Field(min_length=10, max_length=10)


class UserCreateIn(BaseModel):
    username: str = Field(min_length=2, max_length=32)
    password: str = Field(min_length=8, max_length=128)
    role: Literal["safety", "admin", "responsible"]
    must_change_password: bool = True


class ResetPasswordIn(BaseModel):
    new_password: str = Field(min_length=8, max_length=128)


class UserDisabledIn(BaseModel):
    disabled: bool


class SwitchModelIn(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    model_id: str = Field(min_length=1, max_length=64)


class FeedbackReviewIn(BaseModel):
    status: Literal["pending", "confirmed", "rejected"]


class ClearDataIn(BaseModel):
    confirmation: str = Field(min_length=1, max_length=32)
