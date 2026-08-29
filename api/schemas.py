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
