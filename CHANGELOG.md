# 更新日志

本文件记录「智护工地 · 施工安全智能体」的版本演进。语义化分段：核心特性 / 修复 / 工程。
模型评测基线与端到端性能数据见 README 对应章节；规划与对标分析见
`docs/工单闭环与对标改进方案.md`；版本叙事全文见 `docs/版本迭代与开发状态.md`。

---

## [v0.6] — 2026-08-27

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
