# 05 · API 接口

> 返回 [README](../README.md) · 上一节 [04 配置说明](04_配置说明.md) · 下一节 [06 测试与评测](06_测试与评测.md)

`api/` 包是系统的唯一服务入口：FastAPI 接口层复用同一 services 层（零业务逻辑复制），同一进程静态托管 React 前端构建产物（单进程单端口），移动端 / 第三方集成走同一套接口。Swagger 文档：`http://localhost:8000/docs`。

- **启动**：`python -m uvicorn api.main:app --host 0.0.0.0 --port 8000`
- **认证**：`POST /api/auth/login` 取 JWT（HS256，默认 12h），后续请求带 `Authorization: Bearer <token>`；密钥经 `API_JWT_SECRET` 环境变量注入（留空则进程内随机生成，重启后所有登录态失效，仅适合本机开发）
- **权限**：角色门（admin/safety/responsible）+ 服务层动作权限双层校验；账号停用后 token 即时失效（每请求 DB 复核）
- **前端托管**：`frontend/dist` 已随仓库提供（改动前端后 `cd frontend && npm run build` 重建），单进程单端口部署；开发模式可用 `API_DEV_CORS=1` 放行 Vite dev server（localhost:5173）
- **预热**：启动后台预热 YOLO/RTSP 监控/LLM/BGE，全部 best-effort；`API_PREWARM=0` 可关闭（测试/极简部署）

## 资源路由

| 前缀 | 端点 | 说明 |
| --- | --- | --- |
| `/api/auth` | `POST /login` · `POST /change-password` · `GET /me` | 登录 / 改密 / 当前用户 |
| `/api/tasks` | `GET /capabilities` · `POST /media`（影像上报）· `POST /text`（文字建单）· `POST /{id}/run`（发起研判）· `GET /{id}/progress` `/{id}/result`（轮询）· `GET /{id}/detail` `/{id}/agents`（证据链）· `POST /{id}/override`（人工改判）· `POST /enhance-extract`（AI 预填）· `POST /asr-transcribe`（语音转写，登录即可）· `POST /query-chat`（旧对话端点转发薄壳，已标弃用） | 影像/文字上报、进度/结果轮询、证据链、改判 |
| `/api/agent` | `POST /chat`（双层对话入口，支持 `attachments` 影像附件，服务端强制绑定给分析工具；规划上下文含会话内原文轮次与跨会话记忆要点，`agent.*` 可关）· `GET /runs/{id}/progress` `/trace` · `POST /runs/{id}/confirm`（确认/取消/改计划）· `POST /runs/{id}/cancel` · `GET /sessions`（列表）/ `POST /sessions`（新建）/ `PATCH`·`DELETE /sessions/{id}`（改名/归档/删除）/ `GET /sessions/{id}/history` · `POST /uploads`（对话附件上传）· `GET /model-info`（能力信息）· `POST /tts`（语音合成，未配置 501） | 认知层端点（v2.1 六端点 + v2.2 会话/附件/能力），全挂鉴权、跨属主 404 |
| `/api/alarms` | `GET /` · `PATCH /{id}/status` · `POST /{id}/convert-order` | 告警列表 / 误报标记 / 转工单 |
| `/api/orders` | `GET /` `/mine` `/pending-review` `/overdue` · `GET /by-task/{id}/panel` · `POST /by-task/{id}/dispatch` · `POST /{id}/rectification` · `POST /{id}/review` · `POST /{id}/ask`（工单 AI 弹窗：仅本单上下文只读问答，责任人本人或 admin/safety）· `POST /{id}/export` | 派发 / 整改 / 验收 / 逾期 / 问询 / 导出 |
| `/api/reports` | `POST /weekly` · `GET /weekly/preview` · `GET /exports/{name}` | 周报生成与下载 |
| `/api/history` | `GET /records` `/stats-by-date` `/severity-breakdown` `/task-risks` | 历史分析数据源 |
| `/api/admin` | 用户（建号/重置密码/停用）、模型（列表/切换）、知识库（列表/导入 PDF）、推送（状态/测试/捕获）、自检、审计（列表/导出）、纠偏样本（列表/审核/导出） | 全部 admin-only |
| `/api/realtime` | `GET /status` | Hub 运行状态 |
| `/api/ws/realtime` | WebSocket（`?token=&source=`） | 帧广播：`{type:'frame', jpeg, status, level, boxes, alarms, cost_ms, seq}` |
| `/api/media` | 静态媒体 | 上传影像/证据截图直出 |
| `/healthz` | GET | 健康检查（无需鉴权） |

## 错误语义

服务层异常统一映射（`api/main.py` `_install_error_handlers`）：

- `AuthorizationError` → **403**（权限不足）
- `ValueError` → **400**（入参业务错误）
- `ConfigError` → **503**（配置缺失）
- 未处理异常 → **500**（留痕日志，不泄内部细节）
- `/api` 未匹配路径保持 **404**（SPA fallback 不吞 API 层路径错误/穿越探测）

## 实时监测

`config.realtime.enabled=true` 后由 API 进程常驻 Hub 承担视频源推理（后端单推理循环），实时页经 `/api/ws/realtime` 观看——N 个浏览器共享同一路推理，无人观看自动降频保活（`realtime.idle_fps`/`active_fps` 可配）；Hub 接管后同进程自动跳过 `monitor.*` 轮询，绝无双路推理。每帧消息含 JPEG 帧、合规状态、检测框（含 track_id）、告警列表与推理耗时。
