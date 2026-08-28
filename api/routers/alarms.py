"""告警路由：列表/详情/状态标记（含误报）/转工单。

状态口径与 ui/page_admin 一致：new/confirmed/false_alarm/resolved；
「误报标记」即 PATCH status=false_alarm。转工单幂等守卫在服务层
（重复转换 400）。权限 admin+safety（服务层 override 动作再强制）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import require_roles
from api.schemas import AlarmStatusIn
from services import admin_console

router = APIRouter(prefix="/alarms", tags=["alarms"])

_staff = require_roles("admin", "safety")


@router.get("")
def list_alarms(limit: int = Query(200, ge=1, le=500),
                user=Depends(_staff)) -> list[dict]:
    """告警事件列表（新→旧，含证据截图相对路径与 image_abs）。"""
    return admin_console.alarm_events(limit)


@router.get("/{alarm_id}")
def get_alarm(alarm_id: str, user=Depends(_staff)) -> dict:
    row = admin_console.alarm_detail(alarm_id)
    if row is None:
        raise HTTPException(status_code=404, detail="告警不存在")
    return row


@router.patch("/{alarm_id}/status")
def update_status(alarm_id: str, body: AlarmStatusIn,
                  user=Depends(_staff)) -> dict:
    """更新告警状态（误报标记 / 确认 / 关闭）。"""
    admin_console.update_alarm_event(alarm_id, body.status, user.user_id)
    return {"ok": True, "alarm_id": alarm_id, "status": body.status}


@router.post("/{alarm_id}/convert-order")
def convert_to_order(alarm_id: str, user=Depends(_staff)) -> dict:
    """高危告警转整改工单（severity 查级；重复转换/状态不符 400）。"""
    order_id = admin_console.convert_alarm_to_order(alarm_id, user.user_id)
    return {"ok": True, "order_id": order_id}
