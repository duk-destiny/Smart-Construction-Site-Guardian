# 更新日志

本文件记录「智护工地 · 施工安全智能体」的版本演进。语义化分段：核心特性 / 修复 / 工程。
模型评测基线与端到端性能数据见 README 对应章节；规划与对标分析见
`docs/工单闭环与对标改进方案.md`；版本叙事全文见 `docs/版本迭代与开发状态.md`。

---

## [v1.0] — 2026-08-29

### 🎉 首个稳定版：前后端分离架构正式打版

> v0.9 期间连续交付重构四阶段（Phase 0-4，明细见下），全部验收口径
> （含运维面）通过后于本日打版 v1.0。语义化版本自 0.x 进入 1.x：
> 核心能力域（双场景检测、多 Agent 研判、本地 RAG、工单闭环、风险周报、
> 实时 Hub 帧广播、React 前端、FastAPI 接口层）全部稳定可用。

**发布基线**

- pytest：PART1 288 passed + AppTest 分进程 4 passed（全绿）；
- API 34 例（httpx AsyncClient 全端点走查）/ Vitest 组件冒烟 5 例 /
  Playwright 双链路（登录→上报→查结果→整改→验收关键流程 +
  双浏览器实时运维验收）全部 PASS；ruff 全绿；
- 运维验收：双浏览器共享单路推理（3fps 单循环、未随观看者翻倍、
  Hub 单次启动）、引擎切换检测不中断——PASS。

**打版动作**

- 版本号同步：`api/main.py`（/healthz 与 Swagger）、`frontend/package.json`；
- tag：`v1.0`；
- 纳入发布前工作区既有变更（单独成提交）：润色后台线程池化、Orchestrator
  分 agent 超时预算、BGE 分块上限/重叠与会话 LRU、`scripts/backup_db.py`
  在线备份脚本——发布基线回归已含上述变更全绿；
- 回退预案：Streamlit 经典版保留可运行，至 React 版稳定一个迭代周期后下线；
- 已知边界（记 backlog，不阻断打版）：BGE 转 ONNX、OCR 扫描件入库、
  INT8 量化版本达标后的线上切换、答辩材料同步 React 功能面。

## [v0.9] — 2026-08-28

### 重构：前后端分离 · Phase 0 + Phase 1

> 依据 `docs/前后端分离重构提示词.md` 执行前两个 Phase（纯 Python，不涉 API/前端）。
> 目标：修复 ui→services 分层破坏，为 Phase 2 FastAPI 铺路；顺带修掉已探明的
> 安全漏洞与质量项。Phase 2-4（FastAPI / React / 实时链路重做）未排期，待确认。

**Phase 0 · ui→services 分层收敛**

- 新增 8 个服务门面模块（`services/db.py` 连接收口 + `session_entry` /
  `task_entry` / `lookup_service` / `order_service` / `history_service` /
  `diag_service` / `lab_service` / `realtime_entry` / `admin_console`）；
- 10 个 UI 页全部去 `get_conn/init_db/dao` 直连，连接生命周期统一由
  `services.db.scoped()` 托管；grep 验证 `ui/` 零 `from dao`；
- 白名单分情况裁定：`core.compliance.evaluate`（page_agents/report）与
  实时页 severity 高危项选取属**业务计算**，不定为白名单——分别下沉为
  `services.task_entry.evaluate_compliance()` 与
  `services.history_service.raise_critical_alarm()`；保留白名单仅限
  纯展示/常量（`ui/components` 纯视图、`core.yolo_engine.WHITELIST*`、
  page_upload 隐患下拉用的 `SEVERITY`、`core.logging`、
  `core.config.shared_config` 只读、`core.evidence.sanitize_filename`），
  各点位已带 `# 白名单（情况1）` 标记注释，塞业务逻辑前必须先下沉。

**Phase 1 · 安全**

- 上传魔数校验 + 大小上限可配（`config.upload.*`，图片 20/视频 200/PDF 20MB）：
  `core/upload_guard.py`，仅凭扩展名不再放行；
- webhook SSRF 防护：生产仅 https + 拒绝内网地址段；演示模式允许 http 回环；
  `notify.allow_private_webhook` 供内网中继；错误入库前脱敏（抹 URL 查询串）；
- 用户名枚举消除：登录失败统一「用户名或密码错误」，审计明细不再区分；
- 配置出库：`git rm --cached config/config.yaml`，新增 `config.example.yaml`，
  `.gitignore` 排除真实配置，密钥字段支持 `${ENV}` 覆盖；
- 模型切换唯一落 DB（`model_registry.active`），不再运行时回写 config.yaml。

**Phase 1 · 质量**

- 跨线程 SQLite：`_attach_clause_async` worker 自开自关连接；
- Notify 连接泄漏：`push_*`/`test_push` try/finally 关闭自建连接；
- 事务化：`save_result`（6 段写）与 `convert_alarm_to_order`（5 段写）单事务，
  中途失败整体回滚；
- 索引补齐（走 `_INDEX_MIGRATIONS`）：alarm_events / work_orders /
  feedback_samples 四条高频索引；schema.sql 漂移回写（detection_records
  track_id/track_frames、alarm_events image_path/source/clause）+ 表数注释修正；
- `core/paths.py` 路径锚点：任意 cwd 启动不破；DB/URL 存相对 posix 路径；
- 静默异常接日志：llm_engine 各失败路径、orchestrator 抽帧、action polish、
  app 预热等保留降级但留痕；
- `ConfigLoader` 进程级共享实例 + `compliance._build_severity` done-flag，
  消除实时帧每帧重读 YAML；`dao.db.get_conn` 缺省路径改运行时读取。

### 测试

- 全量 **248 passed**（v0.8 237 + Phase0/1 回归 11）；ruff 全绿；
- `test_ui_flows` 三处按新架构重写（模型切换断言 DB 翻转且 config 不变）。

### 重构：前后端分离 · Phase 2（FastAPI 接口层）

> 依据 `docs/前后端分离重构提示词.md` Phase 2 执行（同日接着 Phase 0/1 交付）。

**接口层**

- 新增 `api/` 包（与 ui/ 平级，只 import services；白名单同 Phase 0：
  `core.config.shared_config` 只读 + `core.logging`）：
  - `api/main.py` 应用工厂：`frontend/dist` 存在才挂静态（单进程单端口预留）、
    全局异常映射（服务层自定义 PermissionError→403 / ValueError→400 /
    ConfigError→503 / 其余 500 留痕）、`/healthz`、lifespan 自举
    （`session_entry.ensure_ready()`，api 不直触 core/bootstrap）、
    启动预热与 app.py 同序（`API_PREWARM=0` 可关）；
  - `api/deps.py`：JWT（HS256，默认 12h；secret 取 `API_JWT_SECRET` 环境变量
    > `config.api.jwt_secret`，皆空回退进程内随机值并 warning）+ **每请求
    DB 复核**（`session_entry.user_brief`：停用/删除即时失效、角色取实时值）+
    `require_roles()` 角色门；
  - 7 个 router（auth/tasks/alarms/orders/reports/admin/ws）43 个端点，
    router 零业务逻辑零 SQL，全部复用既有 services 门面；
    `api/uploads.py` 把 FastAPI UploadFile 适配成服务层依赖的
    UploadedFile 鸭子类型（name + getvalue()），业务代码零改动；
  - `/api/ws/realtime` 先落 token 鉴权（4401 拒绝）+ 心跳保活，帧广播 Phase 4 接入；
  - CORS 默认关，仅开发模式（`API_DEV_CORS=1` / `config.api.dev_cors`）
    放行 Vite dev server（localhost:5173）。

**服务层小扩展（供 API 复用，UI 不受影响）**：`session_entry.user_brief /
ensure_ready`；`admin_console.alarm_detail / kb_docs / notify_status /
notify_test_push`；`order_service.export_order_excel`；
`export_service.load_export_file`（下载防穿越门面：basename 归一 + 必须落在
data/exports 内）；`lookup_service.task_detection_detail` 增量附带 task/risk 概览行。

**顺带修复的既有缺陷（同批提交）**

- **`TaskService.start_async_run` 跨线程闭库写**（与 Phase 1 修过的
  `_attach_clause_async` 同型、当时漏网）：worker 捕获调用方 TaskService，
  `scoped()` 模式下请求返回即闭库，后台 `save_result` 必然
  ProgrammingError——异步研判链路在 scoped 化之后实际不可用；改为 worker
  内自开自关连接（`test_async_run` 夹具同步改临时文件库）；
- `export_service` / `report_service` 导出路径 cwd 相对（Phase 1 路径锚点
  统一漏网）→ 统一 `core.paths.data_path("exports")`；
- `scripts/__init__.py` 免疫用户 site-packages 同名 `scripts` 常规包遮蔽
  （常规包恒优先于 namespace 目录，致 tests 无法 import scripts.*）；
- `run_tests.py` 临时目录重定向仅在仓库路径为纯 ASCII 时生效——
  onnx/onnxruntime 原生临时文件层在非 ASCII TMPDIR 下静默失败
  （仓库路径含中文时 test_quantize 必挂）。

**测试**

- 新增 `tests/test_api.py` 27 例（httpx AsyncClient 直连 ASGI）：全端点
  正常路径 + 权限拒绝（safety 访问管理端 403 / responsible 上报 403 /
  无·过期·伪造 token 401 / 停用后 token 即时失效）+ 上传负向
  （魔数不符 / 扩展名与内容不一致 / 超限，注入 0MB 配置）+ 下载防穿越 +
  WS 鉴权 + CORS 开关；临时库逐例隔离，`API_PREWARM=0` 关预热。

### 重构：前后端分离 · Phase 3（React 前端）

> 依据 `docs/前后端分离重构提示词.md` Phase 3 执行；`frontend/dist` 构建产物
> 由 FastAPI 静态托管，单进程单端口；Streamlit 保留为回退入口。

**前端主体（frontend/，React 18 + TypeScript + Vite + AntD 5）**

- 基线：react-router-dom v6 角色路由守卫（responsible 仅「我的整改单」；
  admin 独占管理端；首登 must_change_password 强制改密页不可绕过）；
  axios 拦截器（Bearer 注入 / 401 清登录态跳登录 / 403·4xx 统一 message）；
  echarts 按需封装；无 Redux 等重型状态库；全中文 locale；
- 页面对齐 Streamlit 功能面：登录 / 强制改密 / 统一上报（影像研判+作业票表单、
  文字建单（隐患下拉由 /tasks/capabilities 下发高危置顶）、对话式只读查询）/
  多 Agent 研判（进度 1.5s 轮询 + Steps + 结果面板 + agent_runs 证据链 Timeline）/
  工单闭环（台账行抽屉派发/改判/导出、待验收照片预览+通过驳回、逾期表）/
  历史分析（日期筛选 + 合规率趋势/类别分布图表 + 任务风险表）/ 实时监测
  （Phase 4 前告警只读占位 + 误报/转工单）/ 我的整改单（**手机响应式卡片**，
  拍照/传图提交、驳回原因、倒计时标签）/ 管理端（用户/模型/知识库/推送/自检/
  审计/纠偏七区块）；
- 构建产物零 CDN 引用（离线可用的验收底线），echarts/antd 全本地打包。

**后端小增补（Phase 3 使能，均带测试）**

- `api/routers/history.py`：历史分析四端点（明细/按日聚合/类别分布/任务风险，
  复用 history_service，admin+safety）；
- `api/routers/media.py` + `services/media_service.py`：媒体安全下发
  （防穿越 + 扩展名白名单；认证放宽 Bearer 头或 ?token= 二选一——`<img>`
  无法带 header，仅内网部署语义，代码注释标明）；
- `/tasks/capabilities` 增发 `hazard_options`（severity 查表 + 白名单中文名，
  服务层转发口径与 Phase 0 白名单裁定一致）；
- SPA 深链路 fallback：/assets 挂载 + 未知 GET 回 index.html（防穿越校验，
  API 路由先注册不受影响）。

**测试**

- `tests/test_api.py` +4 例（33 例）：history 四端点及 responsible 403、
  媒体下发与越界/坏扩展名/未登录拒绝、SPA fallback（深链路回 index.html、
  真实文件直出、API 不受影响）、capabilities 含隐患选项；
- Vitest 组件冒烟 5 例（标签映射/登录页渲染/token 注入/401 清登录态；
  Node 25 实验 localStorage 与 jsdom 冲突、matchMedia 缺失均已在 setup 垫平）；
- Playwright 关键流程冒烟（`scripts/api_browser_smoke.py`）：临时库起 uvicorn
  → chromium 走查 登录→强制改密→文字上报→查结果→拍照提交整改→验收销项
  全链路 PASS。

### 重构：前后端分离重构 · Phase 4（实时链路重做，四阶段收官）

**后端唯一推理循环（api/realtime_hub.py）**

- 常驻 daemon 线程汇聚多路帧源（realtime.sources → 回退 monitor.sources →
  demo:// 合成源兜底），每帧 检测→误报过滤→per-source 跟踪→三级合规
  （纯规则轻链路铁律不变），critical 当帧出警（建告警→证据→异步推送→
  条款挂载，复用 history_service 既有链路）+ (源,类别) 冷却去重 + 帧级历史持久化；
- WebSocket `/api/ws/realtime` 推 JPEG（框已标注）+ 违规框摘要 + 告警事件 JSON：
  广播模型为「每源最新帧 + seq 递增」，各 WS 连接独立轮询取新帧——
  **N 个观看者共享同一路推理，推理成本 O(1)**；无人观看降频保活
  （idle_fps 默认 1，有观看者恢复 active_fps 默认 2，可配）；
- 与 monitor_service 收敛：Hub 启动置接管标志（services.realtime_entry），
  ensure_monitor_started 检测到即跳过后台轮询——同进程绝无双路推理；
  Streamlit 进程不受影响仍走旧链路（回退可用）；
- 新增 REST `GET /api/realtime/status`（源清单凭据打码/观看者/计数/当前 fps）。

**core/realtime_engine 两处结构性修复**

- **per-source tracker**：原实现全部源共享一个 IoUTracker——跨摄像头目标
  ID 互相串扰；改按 source_key 惰性建 tracker（双检锁），并解锁多源并行
  analyze 能力（当前 Hub 串行调度，CPU 预算内最稳）；
- **reload build-then-swap**：原实现先清空再逐个 append，检测线程会读到
  半空引擎列表；改局部列表构建成功后原子替换，失败抛出旧引擎组原封不动；
  配合引擎快照读取，admin 切换模型过程中检测不崩（并发测试覆盖）。

**services/task_service 类级可变状态加锁（TOCTOU）**

- start_async_run 的「查再置」存在竞态（两请求同过检查 → 同任务双线程
  研判）；新增 _STATE_LOCK 统一守护 _progress/_task_owners/_async_running/
  _async_results 的全部跨线程读写。

**前端实时页（React）**

- WebSocket 帧广播消费：canvas 渲染标注 JPEG（红/黄/绿边框随合规等级）、
  源选择器、连接状态、帧序/耗时统计；告警当帧弹窗（notification）+
  800Hz 警报音（与 Streamlit 同源的 base64 WAV 资产，客户端 5s 冷却）；
  Hub 未启用自动降级为告警只读列表（Phase 3 行为保留）。

**测试**

- 新增 `tests/test_realtime_hub.py` 10 例：Hub 循环发布/告警落库、open 告警
  去重、观看者降频、多源隔离、接管标志收敛、per-source tracker 隔离、
  reload 换代与并发 detect 不崩、TOCTOU 双线程仅一方启动；
- `test_api.py` WS 用例升级为真实广播断言（hello→frame→ping/pong→
  观看者回落）+ realtime/status 端点两态；API 34 例 + 全量回归绿。

**运维验收与顺带修复**

- 新增 `scripts/realtime_acceptance.py` 运维验收：临时库+内存态配置启动，
  双 chromium 观看同一路 demo:// 源——断言 2 观看者共享单路推理（ polls
  按帧率推进不随人数翻倍）、"已启动"日志恰一条、admin 切换模型（引擎
  reload）过程中推理与前端广播不中断——**PASS**；
- 修复前端登录→强制改密页竞态：事件回调里 await 后紧跟 navigate 会被
  路由状态竞争吞掉（token 已入库但被弹回登录框，且 Playwright page.url
  在 pushState 下不刷新导致误判）——改为登录态上下文驱动路由（Login
  effect）+ 改密页容忍瞬时未同步的登录态（显示同步中而非弹回）。

## [v0.8] — 2026-08-28

### 新增：安全收口 + 账号治理 + 运维闭环（三阶段一次交付）

**P0 · 安全收口**

- **文件名穿越修复（P0-1）**：新增 `core/evidence.sanitize_filename()` 统一
  文件名消毒（basename → 危险字符压制 → 限长，中文文件名不受影响），
  修复管理端 PDF 导入（`page_admin.py`，原实现遇 `/abs` 绝对路径经
  os.path.join 可整路径覆盖前缀）与影像上传（`page_upload.py`）两处
  路径穿越面；上传加显式大小上限（影像 200MB / PDF 50MB）。
- **账号治理（P0-2）**：`AuthService` 新增建用户 / 本人改密 / 管理员重置
  （重置后强制对方改密）/ 停用启用四件套，全部写审计；RBAC 新增
  `manage_users` 动作（仅 admin）；users 表新增 `must_change_password` 与
  `disabled` 列（老库自动 ALTER，schema 同步）——停用在登录与
  `check_permission` 双侧即时生效；守护约束：不能停用自己、最后一名可用
  管理员不可停用。种子默认账号带初始密码标记，`security.force_default_pwd_change`
  门控二态：true=首登强制改密，false（默认）=顶栏常驻提醒不阻断演示；
  顶栏新增「🔑 修改密码」入口，管理端新增「用户管理」区块（列表/建号/
  重置/停启）。全系统此前无任何改密与建用户入口的缺口就此补齐。
- **密钥环境变量展开（P0-3）**：`ConfigLoader` 支持全部字符串值
  `${ENV_VAR}` / `${ENV_VAR:-默认值}` 展开，`notify.webhook_url`、
  `asr.api_key`、`enhance.cloud.api_key` 可经环境注入不必明文进 git；
  未定义且无默认值的占位保持原样（漏配一眼可见）。
- **CI 门禁（P0-4）**：新增 `ruff.toml` 最小门禁（E9+F：语法错误/未定义名/
  未使用导入），CI 加 ruff（阻断）与 pip-audit（`continue-on-error` 观察）
  两步；本次顺带清理 23 处死代码（未使用导入/死赋值/空 f-string）。

**P1 · 工程健壮性**

- 吞异常留痕：启动自举（`app.py`）、预热线程四段、模型注册种子、
  证据截图/整改照片落盘、告警条款挂载、实时告警触发等关键静默点补
  warning 日志（降级语义不变，排障不再黑盒）；
- RTSP 凭据打码：`core/video_source.mask_source()`（`user:pass@` →
  `user:****@`），实时页三处展示点接入，数据库/内部链路仍存原始 source；
- 登录失败限速字典加容量上限（FIFO 淘汰，防任意用户名灌内存）；
- 异步研判进度按属主隔离：`_task_owners` 登记 + `get_progress` /
  `pop_async_result` / `start_async_run` 属主校验，非属主视角不可见
  （不传 user_id 保持旧行为，兼容内部调用）；
- README 新增「生产部署建议」：默认账号治理、密钥环境注入、HTTPS 反代、
  WAL 三件备份、审计留存 cron、上传限制。

**P2 · 功能迭代**

- **派发即推送责任人**：`NotificationService.push_dispatch`（复用催办
  `wo_<id>` 软引用留痕与同一 webhook 通道），`DispatchService.dispatch_order`
  派单当下即推送——注入 notifier（测试）走同步、真实服务走 daemon 线程
  不阻塞 UI；notify 未启用自动 skipped 留痕，不影响派发主链路；
  `test_push`/`push_overdue`/`push_dispatch` 三路收拢为 `_push_sample`
  单一管线；
- **多 Provider 接入（enhance 多 base）**：`enhance.providers` 列表
  （{name, type(cloud|local), api_base, api_key, model, timeout_sec}），
  **链序即降级序**、逐家过白名单、`total_deadline_sec=30` 全链总预算
  防多家慢超时叠加；`providers` 缺省时由 legacy `provider/cloud` 单槽
  合成等价链零破坏；`LlmEngine.chat()` 通用单轮对话；上传页预填按钮
  标注实际命中的 provider 名；
- **Agent 测试场**（`ui/page_lab.py`，admin/safety 专属）：视觉/规范/
  融合/处置润色四个单 Agent 试跑 + 整链干跑（与研判页同一 Orchestrator，
  **不传 work_order_dao、不调 save_result、不写审计**——干跑不入库铁律，
  演示台账零污染）；处置润色可按所选 base 对比输出；白名单/severity
  纪律照走，测试场不是后门；
- **AI 通道连通性自检**：`EnhanceEngine.check_cloud()`（最小 chat 调用
  一次验证 端点+key+模型）与 `AsrEngine.check_connectivity()`（内存合成
  0.5s 静音 wav 端到端走一次转写）；系统自检页新增「云 LLM 通道 / 云 ASR
  通道 / 本地 Ollama」三行——云通道仅已配置时渲染（延续静默约定，未配置
  不报红），401/403→key 无效、404→端点/模型名排查提示，演示前一键确认
  key 有效；页顶 caption 常驻显示各 AI 通道配置状态；
- **审计导出与受控留存**：`AuditService.export_csv`（区间筛选，只读不碰
  仅追加约束）+ 管理端「审计流水 CSV」下载；`scripts/audit_maintenance.py`
  cron 归档入口——默认只导出不删除（C4 语义默认不破），`--delete` 走
  受控路径：行数校验一致 → 先写 `audit_archive` purge 凭证 → 摘除禁删
  触发器 → 删除 → 原样重建触发器，删档行为可审计可追溯。

### 测试与验证

- 全量 **237 passed**（基线 189 + 新增 48：文件名消毒/ENV 展开/账号治理/
  派发推送/审计导出与受控删档/进度隔离/凭据打码/AI 通道自检/多 Provider
  链与测试场）；ruff 全绿；
- 真实库副本验证：老库 ALTER 迁移幂等、既有账号不受补种影响、
  `audit_maintenance.py` 导出/删档端到端跑通。

## [v0.7] — 2026-08-28

### 新增：🟢 计划内清尾三件

- **评测集资产化**（方案 4.3/5.2 承诺兑现）：`tests/datasets/` 两份 JSON 入库——
  隐患描述提取 30 例（5 隐患键 × 位置有无变体）、只读意图 30 例
  （extract 级 20 + db 级 10，seed_orders 预置固定工单）；
  `scripts/eval_datasets.py` 一键跑命中率，结果写 `data/eval/dataset_eval.json`。
  **实测（本地 qwen3:8b）**：意图路由 **30/30 = 100%**（use_llm=False 确定性口径）；
  提取 core **63.3%**（类别+场景）/位置软指标 **0.3**——8B 模型的真实短板
  如实入表，提升方向即"云 key 接入更大模型"（v0.6 双 Provider 已备好通道）。
- **意图路由三处真缺陷修复**（评测轮揪出）：`` 在中文边界失效致序数
  「2号那单」全漏 → 改"号"后负向断言；「过去两周/上周」时间窗不支持 →
  周解析（含中文"两"）；「还没闭环」被误判已闭环 → 未/没 系正则优先于 已 系。
- **告警→工单桥**（方案 5.1 约束 2 兑现）：`convert_alarm_to_order`——
  severity 查级（critical→较大/warning→一般）、`tasks.source='camera'`、
  幂等守卫以**不可篡改审计流水**为准（实时告警 task_id 恒 None，状态字段
  不可信）；管理端告警卡片一键「📮 转为整改工单」后进入既有派发闭环；
  6 例测试含越权与重复转换负例。

### 量化评测结论（逐类 F1，configured 阈值，独立测试集）

| 模型 | 类别 | FP32 | INT8 | Δ |
| --- | --- | ---: | ---: | ---: |
| fire v2 | 烟雾 | 0.909 | 0.909 | 0 |
| fire v2 | 火花 | 0.794 | **0.725** | -6.9% |
| ppe v3 | 佩戴安全帽 | 0.870 | 0.864 | -0.6% |
| ppe v3 | 未戴安全帽 | 0.732 | **0.762** | +3.0% |
| ppe v3 | 未穿反光衣 | 0.755 | **0.706** | -4.9% |
| ppe v3 | 人员 | 0.620 | 0.586 | -3.4% |
| ppe v4 | 未戴安全帽 | 0.791 | 0.791 | 0 |
| ppe v4 | 人员 | 0.540 | 0.515 | -2.4% |

**结论：INT8 维持 active=0 不切换。** 多数类 ±1%，但火花 -6.9%、未穿反光衣
-4.9% 的类级损失超出 README 原预估（<0.5%）；量化副本定位为**体积敏感的
边缘部署选项**（44.7→11.5MB，3.9x），精度敏感的线上继续 FP32。
这组实测数字本身就是"注册→评测→手动切换"闭环价值的最好答辩证据。

### 修复

- `_page_runner.py` 陈年误删导致 e2e_apptest 全组基建失效（历史恢复）；
- 管理端验收队列首张默认展开（expander 折叠态 rerun 不保持的体验缺陷）；
- 侧边栏入口与页内标题统一为「统一上报」。

## [v0.6] — 2026-08-27 — 2026-08-27

### 新增：二期四项（路线图首批兑现）

- **AI 提取预填 + 云 key 双 Provider**（`services/enhance_service.py`）：
  自由文本 → {hazard_key, scene_id, description, location} 四字段草稿；
  provider=auto 时**云端(OpenAI 兼容 /chat/completions，用户自配 key)失败自动落
  本地 Ollama**，均败退手填表单；`hazard_key`/`scene_id` 越白名单整包弃收
  （与 P2/P3 同构的注入面清零）；输出仅作预填草稿，人工确认后才建单。
  UI：Tab②「⚡ AI 提取预填」按钮按可用性渲染（⛅云端/📦本地 标注）。
- **催办 webhook 化**：`NotificationService.push_overdue` 复用告警通道
  （wecom/dingtalk/generic + 重试 + notification_logs 软引用留痕
  `wo_<order_id>`），`scan_overdue` 双档推送——责任人催办 + 逾期满 24h 越级
  管理层；notify 未启用时自动 skipped（审计流水不受影响）。
- **上传链路异步化**：`TaskService.start_async_run` 后台线程执行重链路
  （含工单落库与异步润色桥），页面 `st.fragment(run_every=2s)` 轮询进度、
  完成自动刷新结果——点击即响应，进度实时可见；同步按钮保留为兼容模式。
- **YOLO INT8 推理量化**：`scripts/quantize_models.py` 动态量化 +
  自动注册（version=`vN-int8`，active=0 不顶替线上）；实测 fire_v2
  42MB→~11MB；上线前经 `evaluate_models.py` 逐类复核。
- **真机验收基建**：`scripts/browser_acceptance.py`（Playwright 八步故事线
  全绿+九截图，data/e2e_screens/）；e2e_apptest 新增 `orders` 组（8 断言）并
  **修复陈年断供**——`_page_runner.py` 曾被误删导致整组基建失效，已从历史恢复；
- **启动预热落地**（方案 5.1 约束①）：检测头模块级单例+锁取代
  `st.cache_resource`（预热线程失效坑），登录后 0 号预热任务构建双场景头，
  完成/失败均打启动日志。
- **产品毛刺**：管理端验收队列首张默认展开（expander 状态 rerun 不保持的
  体验修复）；侧边栏入口更名「统一上报」与页内标题一致。
- 测试新增 23 例（enhance 6 / overdue-notify 3 / async 3 / quantize 2 /
  browser-acceptance 为脚本非用例）。全量 **179 passed**。

## [v0.5] — 2026-08-27

### 新增：对话式查进度（P3 只读路由，闭环叙事最后一块）

- **`services/intent_router.py`**：四层防线落地——规则抽参（哈希工单号 /
  口语序数「3号工单」按最新序映射 / 状态词 / 时间窗 / 逾期与统计词）；
  规则无把握且本地 Ollama 可用时走 `LlmEngine.ask_json` 封闭集分类
  （白名单外意图/字段一律拒收回人工层）；模糊多候选返回 confirm 列表点选。
- **工单速查 Tab 升级为对话式**：问一句出结果卡（状态/责任人/截止/描述）；
  逾期视图、近 N 天概览四指标卡；无把握时兜底展示最新待办并给出问法提示。
- **硬边界不变**：路由全程零写入（测试以审计行计数为证）；
  写操作仍只存在于各页面确认按钮。
- 测试新增 10 例（含"整轮对话会话零 INSERT/UPDATE"的只读铁证、
  越白名单字段拒收、序数越界回人工、LLM 分支 monkeypatch 隔离）。
  全量 **165 passed**。

## [v0.4] — 2026-08-27

### 新增：统一上报三 Tab（P2-v0）+ 可选语音转写（静默策略）

- **上传页原地升级为三个 `st.tabs`（方案文档 4.4 落地）**：
  - **📷 影像研判**：原 图片/作业票 → 重链路 流程原样迁入；
  - **📝 文字上报（P2-v0）**：自由文本线索 → 隐患类别白名单下拉
    （`compliance.severity` 键集合，正向 safe 项排除、高危置顶）→
    `create_text_hazard()` 跳过视觉链路直接落单——风险按规则查表
    （critical→较大 / warning→一般），工单文案复用处置 Agent 等级模板，
    `tasks.source='text'`，台账首次出现 📝 来源；提交后直达报告页派发面板闭环衔接；
  - **🔍 工单速查**：只读列表 + 关键词过滤（读写硬隔离）。
- **语音 = 纯转写调用**：`core/asr_engine.py` 封装 OpenAI 兼容
  `/audio/transcriptions` 一次 multipart 调用（无 requests 新依赖）。
  **静默约定**：`config.asr.enabled/api_base/api_key` 任一缺失时
  `available()=False`，UI 完全不渲染语音入口——不提示不灰显；
  配置后录音 → 转写文本自动回填描述框。不做本地 whisper、不做音频文件上传。
- 测试新增 9 例（ASR 静默/multipart 结构/网络失败降级；文字建单白名单拒绝、
  critical 映射、位置前缀、越权负例、审计字段）。全量 **155 passed**。

### 变更

- 上传页标题更名「统一上报」，导航入口不变。

## [v0.3] — 2026-08-27

### 新增：风险分级周报（P1，复盘层）

- **`services/report_service.py`**：周期聚合三类事实源——检测帧
  （总量/不合规/警告/合规、隐患类别 TOP）、告警（按状态计数）、工单闭环
  （漏斗 + 当前存量逾期数 + **按责任人派发/销项/在办/逾期率画像**）；
  结论行为纯规则拼接（销项率、督办提示、最高频类别、超标预警），零 LLM 参与。
- **中文 PDF 渲染**：fpdf2 + 跨平台 CJK 字体定位器（复用 `tests/cjk_font.py` 候选表），
  四节结构：检测概览 / 告警概况 / 工单闭环指标 / 结论与建议；产物落 `data/exports/`。
- **管理端「风险周报」区块**：起止日期选择 → 指标卡 + 结论列表 + 责任人进度表
  预览 → 一键下载 PDF。
- **生产归档入口**：`scripts/weekly_report.py`——cron 每周驱动，
  stdout 输出 JSON 摘要供运维采集；与 Web 进程解耦（系统调用豁免 UI 权限门，
  与 ExportService 的既有约定一致，登录态调用仍强制 `export` 权限并写审计
  `report_generate`）。
- 测试新增 8 例（固定日期种子全确定性断言：周期过滤、告警口径 CHECK 对齐、
  工单漏斗与存量逾期、责任人逾期率 50% 精确匹配、结论规则命中、PDF 头部与体量、
  审计留痕、权限负例）。全量 **146 passed**。

## [v0.2] — 2026-08-27

### 新增：工单闭环（P0，业务从"开单"走到"销项"）

- **责任人与 RBAC 第三角色**：新增 `responsible`（整改责任人）角色，
  权限仅 `view/rectify`，不进管理端；默认账号 `lisi / demo1234`，
  启动自举升级为**按用户逐个补种**——老库升级自动获得责任人账号，且不触碰既有账号密码。
- **派发到人**：报告页新增「派发与整改闭环」面板。责任人下拉自动预选
  `config.dispatch.rules` 的场景命中建议（自上而下首中即用，可省略 scene 作通配）；
  默认整改时限按风险等级查表（重大1h/较大2h/一般24h/低168h），派发时可覆盖；
  仅 open/rejected 可派发或改派。
- **我的整改单页**（responsible 专属导航）：查看要求/截止/倒计时，
  填写整改说明 + 多照片上传（落 `data/rectifications/<order_id>/`）申请验收；
  驳回单据展示原因并可重新提交。
- **管理端验收队列与逾期巡检**：待验收工单列表核对材料后通过销项或填原因驳回
  （驳回退回 open 留痕可再改）；「扫描逾期并催办」按钮配**时间游标**
  （模拟小时数），现场即可完整演练催办→越级故事线，无需真实等待。
- **生产巡检入口**：`scripts/overdue_scan.py`，与 Web 进程解耦由系统 cron 驱动同一扫描函数。
- **任务来源标记**：`tasks.source ∈ {camera, upload, text}`，历史台账展示输入来源
  （📷实时 / 📤图片 / 📝文字），支撑"机器感知 + 人工上报汇流同一流水线"叙事。
- **审计全覆盖**：dispatch / rectification_submit / review / overdue_notify /
  overdue_escalate 五类动作全部落 `audit_logs`。

### 新增（数据层）

- `work_orders` 增列：assignee_id / status(open→submitted→closed) /
  dispatched_at / deadline / submitted_note / submitted_imgs / approved_by /
  approved_at / closed_at / review_reason（「逾期」为派生态不入库，防状态机膨胀）。
- `tasks.source` 列（DEFAULT 'upload'）。
- 老库 users.role CHECK 约束缺少第三角色时执行**受控表重建迁移**
  （暂存引用视图 → 换表 → 原样重建视图），幂等且数据无损。

### 新增（测试）

- `tests/test_work_order_flow.py` 15 例：规则解析首中即用、权限正反边界
  （responsible 不能验收 / 冒名提交被拒）、完整状态机含驳回重改环、
  固定游标下 scan_overdue 计数确定性、来源标记进台账。全量套件 138 passed。

### 变更

- `MultiSourceMonitor.grab_all` 两阶段化（并行抓帧 IO + 主线程串行推理，
  消除多路 RTSP 延迟相乘）；实例化支持 keep_open 长连接复用。
- `init_db` 按「库路径+schema mtime」记忆化，重复调用 O(1)；
  实时链路热路径补显式连接关闭。
- 登录失败滑动窗口限速（10 次/5 分钟临时锁定）；审计 detail 统一 json.dumps 构造。

---

## [v0.1] — 2026-08-27

基线版本（tag `v0.1` → `ee788b0`）：

- 双场景检测（动火作业 hot_work + 施工 PPE construction_ppe），YOLO ONNX 本地推理，
  双头并行 ~211ms/帧（CPU）；
- 上传研判多 Agent 重链路（视觉∥规范 RAG → 融合 → 复核 → 处置，≤8s，超时降级）；
- 实时监测轻链路（首帧 critical 当帧出警 + 证据截图 + 异步 webhook 推送 +
  后台 RTSP 轮询冷却去重）；
- 本地 RAG（BGE-small-zh 子进程隔离 + ChromaDB，条款防编造噪音过滤）；
- 人工纠偏 → 审核 → confirmed 回写训练集 → 复训 → 模型注册/手动切换 全闭环；
- 通知通道（wecom/dingtalk/generic）+ demo 模式捕获回放；Streamlit 单容器部署、
  GitHub Actions CI、SonarCloud 门禁；
- 安全修复：SQLite 连接泄漏治理、登录限速、审计 JSON 注入修复、RTSP 持久连接并行抓取。
