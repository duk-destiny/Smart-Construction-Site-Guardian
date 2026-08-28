"""WebSocket 实时通道（Phase 4 完整实现帧广播；Phase 2 先落鉴权与连接保持）。

token 经查询参数传入（浏览器 WebSocket 无法自定义 header）；
校验失败以 4401 关闭。连接建立后响应 ping/pong 心跳保活；
Phase 4 在此挂 realtime_hub 的 JPEG 帧广播 + 告警事件 JSON 推送。
"""
from __future__ import annotations

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from api.deps import try_decode

router = APIRouter(tags=["ws"])


@router.websocket("/ws/realtime")
async def realtime_socket(websocket: WebSocket, token: str = Query("")):
    payload = try_decode(token)
    if payload is None:
        await websocket.close(code=4401)
        return
    await websocket.accept()
    await websocket.send_json({
        "type": "hello", "role": payload.get("role"),
        "phase": "placeholder",
        "message": "实时帧广播将在 Phase 4（后端单推理循环 + WebSocket）上线",
    })
    try:
        while True:
            msg = await websocket.receive_text()
            if msg == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        return
