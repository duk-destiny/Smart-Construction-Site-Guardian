"""实时通道（Phase 4 完整实现）：WS 帧广播 + 状态端点。

广播模型：Hub 常驻线程维护「每源最新帧」，本路由的每个 WS 连接独立轮询
latest()，seq 变化才发送——N 个观看者共享同一路推理（后端 O(1) 成本）。
token 经查询参数传入（浏览器 WebSocket 无法自定义 header），非法 4401。
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect

from api.deps import require_roles, try_decode
from api.realtime_hub import get_hub

router = APIRouter(tags=["ws", "realtime"])

_staff = require_roles("admin", "safety")


@router.get("/realtime/status")
def realtime_status(user=Depends(_staff)) -> dict:
    """Hub 运行状态：源清单（凭据打码）/观看者数/轮询与告警计数/当前 fps。"""
    hub = get_hub()
    if hub is None:
        return {"enabled": False, "running": False, "sources": [],
                "viewers": 0, "polls": 0, "alarms": 0}
    return {"enabled": True, **hub.status()}


@router.websocket("/ws/realtime")
async def realtime_socket(websocket: WebSocket, token: str = Query(""),
                          source: int = Query(0, ge=0, le=63)):
    payload = try_decode(token)
    if payload is None:
        await websocket.accept()
        await websocket.close(code=4401)
        return
    # 角色 + 停用复核：与 HTTP 端点 /realtime/status 同口径
    from services import session_entry
    brief = session_entry.user_brief(payload.get("sub"))
    if brief is None or brief.get("disabled"):
        await websocket.accept()
        await websocket.close(code=4401)
        return
    if brief.get("role") not in ("admin", "safety"):
        await websocket.accept()
        await websocket.close(code=4403)
        return
    hub = get_hub()
    await websocket.accept()
    if hub is None or not hub.sources or source >= len(hub.sources):
        await websocket.send_json({
            "type": "unavailable",
            "message": "实时 Hub 未启用或所选源不存在（config.realtime.enabled / monitor.sources）",
        })
        await websocket.close()
        return

    hub.add_viewer()
    try:
        await websocket.send_json({
            "type": "hello", "role": payload.get("role"),
            "sources": hub.source_list(),
            "message": "实时帧广播已连接（后端单推理循环，多端共享）",
        })
        last_seq = -1
        while True:
            state = hub.latest(source)
            if state is not None and state.seq != last_seq:
                last_seq = state.seq
                await websocket.send_json({
                    "type": "frame",
                    "seq": state.seq,
                    "jpeg": state.jpeg_b64,
                    "status": state.status,
                    "level": state.level,
                    "boxes": state.boxes,
                    "alarms": state.alarms,
                    "cost_ms": state.cost_ms,
                    "ts": state.ts,
                })
            # 非阻塞收发：短超时探测断连与心跳，不阻塞帧推送
            try:
                msg = await asyncio.wait_for(websocket.receive_text(), timeout=0.05)
                if msg == "ping":
                    await websocket.send_json({"type": "pong"})
            except asyncio.TimeoutError:
                pass
            await asyncio.sleep(1 / 30)  # 状态轮询节流（帧率由 Hub 侧控制）
    except WebSocketDisconnect:
        return
    finally:
        hub.remove_viewer()
