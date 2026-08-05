# 告警外部推送（P0/P1）说明

> 目标：高危告警不仅停留在系统内，还要自动推送到外部渠道（企业微信 / 钉钉 / 通用 Webhook），
> 并保留现场证据截图与推送留痕，形成「检测 → 告警 → 证据 → 推送 → 可追溯」的闭环。

## 1. 完整链路

```
现场帧（摄像头 / 多路 RTSP / 本地视频）
   │  RealtimeEngine.analyze → 双场景检测头 + 三级合规研判
   ▼
合规级别 == critical（火花/烟雾/未戴安全帽/堆放物倾斜等）
   │  1) create_alarm_event       告警落库 alarm_events（含 scene/cls/conf/source）
   ▼
   │  2) save_alarm_evidence      标注帧存为 JPG → data/alarms/，回填 image_path
   ▼
   │  3) attach_alarm_image       UPDATE alarm_events.image_path
   ▼
   │  4) notify_alarm             NotificationService.push_alarm_async（daemon 线程，不阻塞监测）
   ▼
NotificationService.push_alarm
   │  - 读取 notify.* 配置（enabled/channel/webhook_url/retries/cooldown/image_base_url）
   │  - 构造渠道 payload（wecom markdown / dingtalk markdown / generic JSON）
   │  - urllib POST webhook，失败按 retries 重试
   ▼
notification_logs 留痕（sent / failed / skipped + error）
```

## 2. 触发入口

| 入口 | 位置 | 说明 |
| --- | --- | --- |
| 实时摄像头帧 | `ui/page_realtime.py` | 每次 critical 帧走完整链路（source=camera） |
| 手动多路 RTSP 抓取 | `ui/page_realtime.py` | 每路 critical 源走完整链路（source=该源地址） |
| 后台自动轮询监控 | `services/monitor_service.py` | daemon 线程按 `monitor.interval_sec` 轮询，`(source, cls)` 冷却去重后告警+推送 |
| 管理端测试推送 | `ui/page_admin.py` | 「外部推送 → 发送测试推送」按钮，验证通道连通性 |

## 3. 配置（config/config.yaml）

```yaml
notify:
  enabled: false
  channel: generic        # wecom（企业微信机器人）/ dingtalk（钉钉机器人）/ generic
  webhook_url: ""         # 机器人 webhook 地址
  timeout_sec: 5          # HTTP 超时
  retries: 2              # 失败重试次数（不含首次）
  cooldown_sec: 60        # 后台轮询同源同类告警推送冷却
  image_base_url: ""      # 证据截图公网/内网访问前缀，拼成推送中的可点击图片链接

monitor:
  enabled: false
  interval_sec: 10
  cooldown_sec: 60
  sources: []             # 每项一个 RTSP/本地视频源
```

- `monitor.enabled=true` 时，`app.py` 启动后自动调用 `ensure_monitor_started()` 拉起后台轮询；
- 管理端「外部推送」区块可查看当前配置与推送留痕；
- 实时页「后台自动轮询监控」面板可查看运行状态/轮询次数/告警数，并支持手动启停。

## 4. 去重与冷却

- 摄像头路径：沿用告警生命周期去重（同一会话同一类别未关闭告警不重复创建）；
- 后台轮询路径：按 `(source, cls)` 冷却去重（`monitor.cooldown_sec` / `notify.cooldown_sec`），
  持续违规可周期性重复告警，而不是等人工关闭后才再报；
- 推送失败会按 `retries` 重试，最终状态写入 `notification_logs`，管理端可追溯。

## 5. 数据表

- `alarm_events`：新增 `image_path`（证据截图相对路径）、`source`（来源：camera / RTSP 地址 / 测试）；
- `notification_logs`：每次推送一行（alarm_id、channel、status、error、created_at），随业务数据一起可被「清空全部监测数据」清理。

## 6. 关键代码

| 模块 | 职责 |
| --- | --- |
| `core/evidence.py` | 标注帧存 JPG，返回相对路径 |
| `services/notify_service.py` | payload 构造 + urllib 推送 + 重试 + 留痕 |
| `services/monitor_service.py` | 后台 RTSP 轮询线程 + 冷却去重 |
| `services/task_service.py` | `raise_alarm`（创建→证据→推送）编排 |
| `dao/models.py` | `AlarmEventDAO.get_by_id/set_image`、`NotificationLogDAO` |

## 7. 验证

```powershell
./run_tests.ps1          # 全量测试（含 tests/test_notify.py 8 个用例）
```

- 推送未启用/未配 webhook → 记 `skipped`，不影响告警链路；
- mock urlopen 成功 → 记 `sent`；HTTP/errcode 失败 → 重试后记 `failed` 并带错误信息。
