# 海之子 · 动火作业安全智能体

> 当前覆盖**动火作业**与**施工 PPE** 两个场景的本地化安全智能体：支持**上传研判**（多 Agent 重链路）与**实时摄像头监测**（轻链路）双模式，全程零外网依赖，可离线部署。

[![Quality gate status](https://sonarcloud.io/api/project_badges/measure?project=duk-destiny_Smart-Construction-Site-Guardian&metric=alert_status&token=820ed34b7f80191064245ea5090a00e98eb45623)](https://sonarcloud.io/summary/new_code?id=duk-destiny_Smart-Construction-Site-Guardian)[![Maintainability Rating](https://sonarcloud.io/api/project_badges/measure?project=duk-destiny_Smart-Construction-Site-Guardian&metric=sqale_rating&token=820ed34b7f80191064245ea5090a00e98eb45623)](https://sonarcloud.io/summary/new_code?id=duk-destiny_Smart-Construction-Site-Guardian)[![Lines of Code](https://sonarcloud.io/api/project_badges/measure?project=duk-destiny_Smart-Construction-Site-Guardian&metric=ncloc&token=820ed34b7f80191064245ea5090a00e98eb45623)](https://sonarcloud.io/summary/new_code?id=duk-destiny_Smart-Construction-Site-Guardian)

一套从「视觉感知 → 规范检索 → 风险定级 → 闭环处置 → 人工纠偏 → 复训回写」端到端打通的动火作业安全智能体，兼顾**研判深度**（多 Agent 协同、RAG 条款引用、证据链可追溯）与**告警时效**（实时链路首帧出警、规则驱动、无阻塞推理）。

## 核心特性

- **双模式**：上传研判走多 Agent 重链路（深度推理、可读工单），实时监测走轻链路（低延迟、连续预警）。
- **多场景检测**：当前覆盖动火作业（火花/烟雾/灭火器）+ 施工 PPE（安全帽/反光衣/人员）两个场景，检测头按场景配置可扩展。
- **三级合规**：红（不合规/即时高危）/黄（警告）/绿（合规），分级规则数据驱动，可不改代码调整。
- **多 Agent 编排**：视觉 ∥ 规范 → 融合 → 复核 → 处置，线程并行 + 超时降级 + 证据链落库。
- **本地 RAG**：BGE 中文 Embedding + ChromaDB 向量检索，规范条款语义匹配、可引用条款号。
- **可选本地 LLM**：Ollama `qwen3:8b` 润色处置文案，断网/不可用自动降级为模板，主链路不受影响。
- **人工纠偏闭环**：改判/逐目标纠偏 → 反馈样本落库 → 审核 → 生成候选训练数据 → 复训回写。
- **告警生命周期 + 外部推送**：高危帧自动创建告警事件、证据截图留存、异步推送到企业微信/钉钉/通用 Webhook，按 `(source, cls)` 冷却去重。
- **模型版本管理**：版本注册、新旧指标对比、一键切换（管理端 UI）。
- **工程化**：服务层权限校验、审计日志、统一测试/启动脚本、Docker 容器化、GitHub Actions CI。
- **内建自检与演示模式**：系统自检页一键跑全链路清单（模型/源/webhook/DB/假告警→推送）；notify 演示模式免 webhook 回环捕获；视频源支持 `demo://` 合成帧，无 key/无摄像头即可自检，找出问题根源。

## 系统架构

### 上传研判 · 多 Agent 重链路

```mermaid
flowchart LR
    A["图片/视频 + 作业票"] --> B["视觉 Agent<br/>YOLO ONNX 推理"]
    A --> C["规范 Agent<br/>RAG: BGE + Chroma"]
    B --> D["融合 Agent<br/>风险矩阵定级"]
    C --> D
    D --> E["复核 Agent<br/>证据/置信复核"]
    E --> F["处置 Agent<br/>工单 + LLM 润色"]
    F --> G["证据链落库<br/>agent_runs"]
```

- 视觉与规范 **线程并行**预检（`ThreadPoolExecutor`，视觉 3s / 规范 4s 超时降级）；
- 规范 Agent 先只查作业票，拿到视觉证据后再补一次 RAG 条款检索，避免重复跑完整规范链路；
- 每个 Agent 的输入/输出摘要、状态、耗时、异常写入 `agent_runs` 表，供协同追溯与答辩演示。

### 实时监测 · 轻链路

```mermaid
flowchart LR
    S["摄像头帧 / 多路 RTSP"] --> D["双场景检测头联合检测"]
    D --> F["误报过滤 + IoU 跟踪"]
    F --> C["三级合规研判"]
    C -->|critical| A["红框 + 800Hz 声音 + Toast + 告警事件"]
    C -->|warning| W["黄框提示"]
    C -->|safe| G["绿框"]
    A --> N["异步外部推送 Webhook"]
```

- 实时态决策链 检测 → 规则合规 → 告警 全程纯规则，**不调用 RAG / LLM、不生成工单**；告警当帧即出，告警落库后异步回填规范条款（非阻塞、不进决策路径），满足低延迟连续监测；
- 首帧 `critical` 当帧出红框、声音与告警事件，无多帧确认门控；
- IoU 跟踪分配稳定 ID 与连续帧数（仅作元数据，不阻塞告警）；
- 同源同类短时间重复告警按冷却自动去重，持续违规可周期性再报。

## 检测能力（当前双场景）

| 场景 | 检测类别 | 模型 | 推荐阈值 |
| --- | --- | --- | --- |
| `hot_work` 动火作业 | 火花 / 烟雾 / 灭火器 | YOLOv8s ONNX `yolov8_fire_smoke_v*.onnx` | 0.35（烟雾 F1=0.91、火花 F1=0.78） |
| `construction_ppe` 施工 PPE | 安全帽 / 未戴帽 / 反光衣 / 未穿衣 / 人员 | YOLOv8 ONNX `ppe_yolov8_v*.onnx` | 0.25（no_helmet F1=0.73、no_vest F1=0.73） |

场景配置见 `config/config.yaml` 的 `scenes.*`，检测白名单与中文释义见 `core/yolo_engine.py` 的 `WHITELIST` / `WHITELIST_CN`。

## 三级合规

| 级别 | 颜色 | 含义 |
| --- | --- | --- |
| 不合规 | 红 | 即时高危（火花/烟雾/未戴安全帽等），红框高亮 + 声音警报 |
| 警告 | 黄 | 需关注（易燃物/未穿反光衣等） |
| 合规 | 绿 | 无违规目标或仅检出安全信号（已佩戴安全帽/反光衣/人员在场） |

分级规则数据驱动，见 `config/config.yaml` 的 `compliance.severity`，键为项目隐患键，值为 `safe/warning/critical`，可在不改代码的前提下调整。

## 多 Agent 职责

| Agent | 角色 | 职责 | 是否调模型 |
| --- | --- | --- | --- |
| 视觉 | 巡检员 | YOLO 逐帧/逐图检测，映射违规中文描述 | ✅ YOLO ONNX |
| 规范 | 资料员 | 作业票字段检查 + RAG 规范条款检索与匹配 | ✅ BGE Embedding + Chroma（第二趟） |
| 融合 | 安全主管 | 风险矩阵查表定级 + 误报过滤（火花低置信/矛盾框） | ❌ 纯规则 |
| 复核 | 质检员 | 高风险低置信项/条款未命中 → 标记人工复核 | ❌ 纯规则 |
| 处置 | 督办员 | 组装整改工单 + 工人白话提示（模板为主，LLM 可选润色） | ⚠️ LLM 可选，降级为模板 |

编排器 `agents/orchestrator.py`：DAG 为 `[视觉 ∥ 规范] → 融合 → 复核 → 处置`，顶层 try/except 兜底，任一 Agent 崩溃标红不退出进程，整体状态取 `failed > degraded > success`。

## 人工纠偏闭环

- 安全员改判或逐目标纠偏后自动写入 `feedback_samples`（`pending`）；
- 管理端可审核 `pending / confirmed / rejected`；管理端「导出纠偏样本 CSV」含 `status` 列，不筛即完整审计流水，筛 `confirmed` 即已审核训练子集；
- `scripts/build_feedback_dataset.py` 将 **仅 `confirmed`** 的样本转为场景级 YOLO 数据（`data/feedback_training/yolo/`），防止未审核纠偏污染模型；
- `scripts/prepare_combined_dataset.py` 已接入纠偏数据源：复训前合并训练集时，`fb_` 前缀的纠偏样本会并入 `data/combined/<scene>/train/`，与原始数据集一同进入复训——闭环「识别 → 纠偏 → 审核 → 回写训练集 → 复训」端到端打通；
- `ui/correction_workbench.py` 提供可视化逐目标纠偏工作台（原图 + 检测框 + 修正框）。

> 改判、审核、CSV 导出均在管理端 UI 完成（无需命令行）；生成 YOLO 训练集与复训走 `scripts/`（训练前批量操作）。

## 告警与外部推送

- 高危帧自动创建告警事件 → 现场标注帧截图留存（`data/alarms/`）→ 异步推送到企业微信/钉钉/通用 Webhook；
- 推送结果写 `notification_logs` 留痕（sent / failed / skipped），管理端可测试推送并追溯；
- 支持多路 RTSP 后台自动轮询（`monitor.*` 配置），按 `(source, cls)` 冷却去重，持续违规可周期性告警；
- 默认关闭，配置见 `config/config.yaml` 的 `notify.*` / `monitor.*`。

## 模型版本管理

- 管理端复训任务支持启动/轮询/早停/导出 best.pt 为 ONNX，训练完成自动注册到模型版本表（`active=False`，不动当前线）；
- 新旧指标对比展示，可一键切换为当前版本；
- `scripts/register_model.py` / `scripts/switch_model.py` 为命令行注册/切换入口；
- `scripts/evaluate_models.py` 在测试集上计算各隐患类别 Precision/Recall/F1，写入 `data/eval/model_eval.json`。

## 目录结构

```
hzz-fire-safety/
├── app.py                  # Streamlit 入口（st.navigation 多页路由 + 全局主题）
├── run_tests.ps1           # 统一全量测试
├── run_app.ps1             # 统一应用启动
├── Dockerfile / docker-compose.yml  # 容器化部署
├── .github/workflows/ci.yml         # 自动测试
├── config/
│   ├── config.yaml         # 全局与多场景配置（权重/类别映射/三级合规/告警/监控）
│   └── rules/              # 各场景风险融合矩阵（hot_work / construction_ppe）
├── core/                   # 最底层：推理引擎与工具
│   ├── yolo_engine.py      # YOLOv8 ONNX 推理（文件/帧两种输入）
│   ├── compliance.py       # 三级合规研判（数据驱动）
│   ├── realtime_engine.py  # 实时轻链路：多场景头联合检测 + 绘图
│   ├── tracker.py          # IoU 目标跟踪（稳定 ID + 连续帧数）
│   ├── false_positive.py   # 误报过滤（PPE 矛盾框 / 烟雾-反光衣冲突）
│   ├── rag_engine.py       # BGE Embedding + ChromaDB 向量检索
│   ├── llm_engine.py       # 本地 Ollama LLM（可选，自动降级）
│   ├── config.py / pdf_parser.py / video_utils.py / video_source.py / yolo_adapter.py
├── agents/                 # 多 Agent 编排（视觉/规范/融合/复核/处置/编排器）
├── services/               # 认证/审计/任务/权限/模型/监控/通知/导出/训练/知识库
├── dao/                    # SQLite 持久化（models.py + schema.sql）
├── ui/                     # 页面（login/upload/realtime/agents/report/history/admin/diag/theme）
├── scripts/                # 数据准备、训练、评测、纠偏、模型注册/切换脚本
├── docs/                  # 阶段规格与答辩材料（可选阅读）
├── data/
│   ├── models/             # 推理权重（ONNX）
│   ├── kb/ uploads/ exports/ alarms/ train/  # 运行期生成目录（多为 .gitignore 忽略）
│   └── app.db              # SQLite（运行期生成）
└── tests/                  # 单元/集成/e2e/反馈闭环/告警生命周期/权限/模型注册测试
```

## 快速开始

```bash
# 1. 安装依赖（离线优先，见 requirements.txt）
pip install -r requirements.txt

# 2. 下载 BGE 中文 Embedding 模型（仅首次，约 100MB，需联网；之后离线可用）
python scripts/setup_models.py

# 3.（可选）本地 Ollama 拉取 qwen3:8b，用于工单文案润色；不装则自动降级为模板
ollama pull qwen3:8b

# 4. 启动（默认 0.0.0.0:8501；首次启动自动建库并种子默认账号）
streamlit run app.py --server.address 0.0.0.0 --server.port 8501

# 5. 全量单元/集成测试
python scripts/run_tests.py

# 6. UI 端到端测试（Streamlit AppTest，无需浏览器）
python scripts/e2e_apptest.py --group safe       # 登录/上传/历史/管理端/LLM
python scripts/e2e_apptest.py --group agents     # 多Agent研判/导出/改判
python scripts/e2e_apptest.py --group realtime   # 实时页 demo:// 源
python scripts/e2e_apptest.py --group diag       # 系统自检
python scripts/e2e_apptest.py --group nav        # 页面切换
```

首次启动自动建表并写入两个默认账号（`core/bootstrap.py`，幂等）：
- 管理员 `admin` / `admin123`（全权限，含管理端 + 系统自检）
- 安全员 `safety` / `demo1234`（上传/研判/改判，不可进管理端）

权限分层由 `services/permission_service.py` 强制校验。

页面导航（`st.navigation` 侧边栏）：上传与作业票 📤 → 多 Agent 研判 🤖 → 工单/改判/导出 📋 → 实时摄像头监测 📷 → 检测历史与分析 📊 → 管理端 ⚙️ → 系统自检 🩺。

实时摄像头页面使用 `st.camera_input` 零依赖轮询方案：点击捕获帧即检测并展示，开启「连续监控」后自动刷新等待下一帧；**声音警报仅在实时监测态、且不合规时触发**。页面同时支持多路 RTSP / 本地视频源按帧抓取。

## Docker / CI

```bash
# 构建并启动容器（默认 8501）
docker compose up --build

# 容器内运行全量测试
docker compose run --rm app python -m pytest tests -q --tb=short -p no:cacheprovider
```

`.github/workflows/ci.yml` 会在 push / pull request 时于 Ubuntu 上安装中文字体并运行全量测试。

> Docker 部署：`docker-compose.yml` 挂载 `./data:/app/data`，故宿主先运行 `python scripts/setup_models.py` 下载 BGE 后容器即可复用（知识库向量库已随仓库入镜像）；首次启动容器内 `core/bootstrap.py` 自动建库并种子默认账号。

> 构建说明：Dockerfile 使用 `python:3.13-slim-bookworm`，并将 apt 源切到 `mirrors.aliyun.com`，避免 `deb.debian.org` 在当前网络环境不可用导致构建失败。

## 配置说明

全局配置见 `config/config.yaml`，主要段落：

| 段 | 作用 |
| --- | --- |
| `models` | 默认火情模型路径与类别映射 |
| `kb` | 规范 PDF 目录、Chroma 集合与持久化路径 |
| `infer` | 置信度/NMS 阈值、抽帧参数、ONNX 线程上限 |
| `llm` | 本地 LLM 开关、Ollama 地址、模型名 |
| `scenes.*` | 各场景检测头列表 + 知识库集合 + 风险矩阵 |
| `compliance.severity` | 隐患键 → safe/warning/critical 分级 |
| `notify` | 外部推送开关/通道/webhook/冷却 |
| `monitor` | 后台 RTSP 轮询开关/采样间隔/源列表 |

场景化后优先读 `scenes.<scene>.yolo_weights`，单模型兜底读 `models.yolo_onnx`。

## 经典 QA（设计取舍）

> 下面这些「为什么这么设计」的问题，答案都能在代码里找到依据，不是话术。

**Q1 为什么分「上传研判重链路」和「实时监测轻链路」两条路？**
答：两条链路的优化目标根本不同。上传态追求**研判深度**——多 Agent 协同、RAG 条款引用、证据链落库、工单闭环，可以接受 3–8s；实时态追求**告警时效**，首帧 `critical` 必须当帧出红框+告警。`core/realtime_engine.py` 的 `analyze()` 注释写死了实时不变量：「analyze 与告警之间不得插入 LLM/RAG/工单等阻塞推理，首帧 critical 即返回，无多帧确认门控」。用一套链路同时满足两个目标，必然两头不讨好。

**Q2 实时轻链路为什么没必要接入 Agent 编排或大模型？**
答：四个原因，都来自代码而非推断——
- **帧率预算**：实时每帧只有几十 ms 预算，LLM 单次生成秒级、RAG 检索百 ms 级，塞进去直接打穿「采样节奏 ≠ 告警 SLA」，事故发生了告警还没出；
- **确定性优先**：实时态要的是「检出 spark/smoke → critical → 出警」这种 100% 可重复的判定，不是创造力；LLM 的「润色」对告警判定零增益，反而可能把置信度 0.3 的火花判成「安全」；
- **编排开销×帧率=纯浪费**：Agent 编排（`ThreadPoolExecutor`、超时降级、progress 推送）是为一次性深度研判设计的重设施，每帧跑一次编排等于把固定开销乘以帧率；
- **代码佐证**：`realtime_engine.py` 全程不 import `llm_engine`/`rag_engine`/`agents`，`analyze()` 只调 `core/compliance.py` 的 `evaluate()`（纯规则查表）+ `core/false_positive.py` 过滤 + `core/tracker.py` 跟踪。需要「说人话」的处置建议，已在告警落库后异步做（见 `action_agent.polish()` 后台线程），不阻塞帧。

**Q3 上传链路里，融合 Agent 和复核 Agent 为什么不引入 LLM？**
答：因为这两个环节做的是**影响处置动作的硬决策**，必须可解释、可审计、可复现，而 LLM 天然不可解释。
- **融合 Agent**（`agents/fusion_agent.py`）：职责是「查风险矩阵取最高等级 + 误报过滤」，是查表 + 阈值比较（`core/false_positive.py` 的 PPE 矛盾框/烟雾-反光衣冲突）。LLM 在这里只会引入幻觉，把确定性的「spark 置信度 0.3 < 0.55 → 判光斑误报」变成不可预测的猜测；
- **复核 Agent**（`agents/review_agent.py`）：职责是「高风险/低置信/证据不足 → 标记人工复核」，判定逻辑是确定规则（`cls ∈ HIGH_RISK_CLASSES` 且 `conf < 0.55`）。规则没覆盖的本来就该交人工，让 LLM 拍板「要不要复核」反而增加不可解释性、模糊审计边界。
- 安全系统铁律：定级、是否复核这类硬决策用规则；LLM 只做不影响判定的软任务（文案润色，见 Q4）。两个 Agent 全程无 `llm_engine` import。

**Q4 那 LLM 到底用在哪？为什么只用在这一处？**
答：**只用在处置 Agent 的「工人提示文案润色」**（`agents/action_agent.py` 的 `polish()`），且是工单落库后异步后台线程，不进 ≤8s 主链路。理由：把规范条款 + 整改要求翻译成一线工人听得懂的大白话是生成式任务，规则写死会僵；但润色结果不影响 `risk_level`/是否告警，`llm_engine.polish()` 返回 `None` 即自动降级为模板，主链路零影响。现状（已在「后续优化计划」标注）：`orchestrator` 当前未注入 `work_order_dao`，`polish()` 早返回，LLM 处于「接线待通」状态，主链路跑模板。

**Q5 视觉与规范为什么要并行 + 两阶段 RAG？**
答：并行是为了压总时延，两阶段是为了省一次向量库往返。
- **并行**：视觉 3s / 规范 4s 用 `ThreadPoolExecutor` 并行 `submit` + `future.result(timeout)` 精确超时，总链路压在 ≤8s，超时转 `degraded` 不崩进程（`agents/orchestrator.py` 的 `_safe()`）；
- **两阶段 RAG**：规范 Agent 一阶段只查作业票字段（快），拿到视觉违规描述后再补一次 RAG 条款检索（视觉 `violation_descs` 回灌 `rule`）。避免每张图都跑一次完整规范检索，`skip_rag` 标志控制是否触发第二趟（`agents/rule_agent.py`）。

**Q6 误报过滤为什么用规则而不是让 LLM 判别？**
答：`spark` 低置信光斑、PPE 矛盾框（同一人既 `helmet` 又 `no_helmet`）、烟雾-反光衣冲突，都是确定性的逻辑冲突，规则秒判且可解释；LLM 逐帧判误报会吃掉帧率预算，还会把可解释的「为什么过滤」变成黑盒。代码在 `core/false_positive.py` 的 `filter_ppe_contradiction` / `filter_smoke_vest_conflict`。

**Q7 模型切换为什么不自动顶替线上、要手动确认？**
答：安全系统里新模型 mAP 提升不等于所有现场场景都更优，自动顶替有回退风险。当前设计：复训完 `register(active=False)` 不立即激活 → 展示新旧 mAP 对比 → 人工「一键切换」 → `model_service.switch()` 回写 `config.yaml` + `_reload_running_engines()` 热加载，留出对比和回滚窗口（见 `ui/page_admin.py`）。

**Q8 整个项目为什么几乎每个选型都偏「轻量」？（Ollama / ONNX / BGE+Chroma / Streamlit / SQLite）**
答：因为项目定位是「工地一线、断网可用、单机部署」的安全智能体，这个定位决定了每个组件都必须满足「装得少、跑得动、断网能活」，不是图省事，而是约束倒逼的择优：
- **视觉推理用 ONNX Runtime 而非 PyTorch 直推**：部署免装 torch/ultralytics 重栈（2GB+），单库跨平台，`run()` 释放 GIL 利于 CPU 双头并行，实测 ~968ms 满足连续监测；`intra_op` 线程可控，实时引擎按 `cpu // 引擎数` 封顶 `intra_op_threads` 防抢核（`realtime_engine.py` 的 `_compute_intra_op()`），小模型线程过多反而更慢；
- **Embedding 用 BGE-small-zh（~100MB）而非 OpenAI/BGE-large**：100MB 模型 CPU 即推理，专为中文优化，与规范条款的中文语义匹配优于通用英文模型；OpenAI 要外网且数据出境，large 要 GPU，都违背工地离线硬约束；
- **向量库用 ChromaDB（嵌入式）而非 FAISS/Milvus**：`PersistentClient` 自带落盘到 `data/kb/chroma/` 免运维，`get_or_create_collection` 开箱即用；FAISS 要自管索引元数据且无持久化，Milvus 要起独立服务，对单机演示是过度工程；
- **前端用 Streamlit 单体而非 Flask+React/Gradio**：原生 `st.navigation` 多页路由 + 会话状态开箱即用，`st.camera_input`/`st.file_uploader`/`st.image`/`st.toast` 直接覆盖「拍照→检测→红框展示→告警弹窗」全链路，单容器部署无前端构建链；Gradio 缺摄像头轮询与会话级状态，React 要 webpack/node 工具链。代价是无精细 CSS 控制，但安全场景重在功能闭环而非像素级 UI；
- **LLM 用 Ollama qwen3:8b（可选）而非云端 API**：独立进程 keep_alive 常驻热调用 ~3s，仅与 `localhost:11434` 通信，`available()` 健康检查不过即返回 `None` 触发模板降级，主链路零依赖；云端要外网，规范数据不出工地，合规与隐私。这是「可选增强、断网降级」而非核心依赖；
- **持久化用 SQLite（单文件）而非 PostgreSQL**：WAL 模式单文件免服务进程，对单机演示无意义起 PG。
代码佐证：`llm_engine.py` 只与 `localhost:11434` 通信、`rag_engine.py` 全本地、`yolo_engine.py` 纯 onnxruntime，整条推理链无一处调外部 API；`Dockerfile` 单镜像跑完，不需多服务容器编排。轻量选型的代价是「组件可能不可用」，但代码层全部做确定性兜底——LLM 挂了走模板、BGE 挂了跳过 RAG、某权重缺了跳过该头不崩，系统不因可选组件缺失而瘫痪。

**Q9 为什么 Agent 编排和链路是手写，而不是用 LangChain / LangGraph / AutoGen 等框架？**
答：因为本项目的编排需求极简，且对确定性、可审计要求极高，框架带来的抽象与依赖成本大于收益。四个理由都来自代码，不是话术：
1. **DAG 是定长静态直链，不需要框架的动态图引擎**。链路固定为 `[视觉 ∥ 规范] → 融合 → 复核 → 处置`（`agents/orchestrator.py` 顶部注释），无循环、无条件分支、无工具调用循环。LangGraph 的核心价值是状态机 + 循环 + 人在回路，对一条直链是杀鸡用牛刀；手写 `ThreadPoolExecutor.submit` + `future.result(timeout)` 二十来行就实现了视觉/规范并行与精确超时降级（`_safe()`）。
2. **确定性优先于「智能」，LLM 不进判定路径**。定级、复核是硬决策用规则（见 Q3、Q6），全链路零 LLM 介入判定；LangChain 的核心抽象（Tool / ReAct / Memory）是为 LLM 动态决策设计的，引入它就得背一套 prompt 编排、token 计费、可观测栈，而本项目根本不靠 LLM 做决策。LLM 只在处置文案润色异步用一处（见 Q4），一个 `polish()` 函数够了，不需要 agent 框架。
3. **可审计、零魔法**。`agents/base.py` 用 `AgentBase` ABC + `AgentMessage` dataclass 信封，强制「禁止裸 dict 跨 Agent」，每个节点的 `status`/`cost_ms`/`error` 都可落库追溯；`_safe()` 把超时/异常统一转 `degraded/failed` 不崩进程。这套契约不到 60 行、行为完全可见；换成 LangChain 的 Runnable/LCEL，链路被框架中间件包裹，出问题要先翻框架源码才能定位是哪一跳挂了——对安全系统这是不可接受的调试成本。
4. **依赖与部署更轻**。`requirements.txt` 零 `langchain`/`langgraph`/`autogen`/`crewai` 依赖，部署只装 streamlit/onnxruntime/chromadb；引入 LangChain 会拖入 langchain-core 及一堆 optional 包，与 Q8 的轻量约束直接冲突。
> 一句话：编排就是「并行 + 超时降级 + 异常兜底」三件套，标准库 `concurrent.futures` 即可表达；框架解决的是「LLM 动态决策的复杂性」，而本项目恰恰把 LLM 关在判定路径之外，所以手写更短、更可控、更可审计。

**Q10 人工纠偏的具体流程是怎样的？多次复训会不会重复堆积、污染源数据集？**

答：闭环分五步，每一步都在代码里有据可查，不是话术：

1. **采集（`ui/page_report.py`）** —— 上传研判报告页有两个纠偏入口：
   - 人工改判：选改判风险等级 + 填原因 → `save_feedback_sample(feedback_type="override")`，记录「自动等级 → 改判等级」；
   - 逐目标纠偏：`ui/correction_workbench.py` 工作台对每个检测框标「误报 `is_fp` / 修正类别 `corrected_cls`」→ `save_feedback_sample(feedback_type="detection_fix", detections=..., corrected_labels=...)`。
   - 写入 DB `feedback_samples` 表，初始 `status=pending`，需 `override` 权限，未审核不进训练。
2. **审核（`ui/page_admin.py`）** —— 列出全部样本，逐条改状态 `pending/confirmed/rejected` → `review_feedback_sample()`；可展开可视化复核再修正 → `update_feedback_corrections()`；一键导出 CSV（含 `status` 列，不筛即审计流水、筛 `confirmed` 即训练子集）。**只有 `confirmed` 才进训练。**
3. **生成 YOLO 数据（`core/feedback_dataset.py` → `scripts/prepare_feedback_training.py`）** —— `write_feedback_dataset()` 先过滤 `status != "confirmed"` 的样本（`not_confirmed` 计数跳过），再按检测类别自动归场景（fire/ppe），复制图片 + 写项目统一类别标注到 `data/feedback_training/yolo/<scene>/`。
4. **并入统一训练集（`scripts/prepare_combined_dataset.py`）** —— `fb_` 前缀的纠偏源与 `data/raw` 各原始源一起合并到 `data/combined/<scene>/`。
5. **复训（`scripts/train_combined.py` / 管理端复训按钮）** —— 用合并集训练 → 导出 ONNX → `register(active=False)` 自动注册 → 管理端下拉框选版本一键切换 → 回写 `config.yaml` + `RealtimeEngine.reload()`（见 Q7）。

**关于「多次复训会不会重复堆积」——已解决，不会：**
- 合并是**幂等**的。`prepare_combined_dataset.py` 的 `_prepare_spec()` 按目标文件名判重：`if dst_img.exists() and dst_lab.exists(): stats["already_exists"] += 1; continue`；`_link_or_copy()` 也先 `if dst.exists(): return`。
- 纠偏样本文件名是 `{scene}_{task_id}_{id}.jpg`（`feedback_dataset.py`），`task_id + id` 唯一，所以重复跑 `prepare_combined_dataset.py` 只会记 `already_exists` 跳过，不会新增重复样本。

**关于「会不会污染源数据集」——已解决，不会：**
- 纠偏数据只写进**生成目录** `data/combined/`，**永不回写 `data/raw/`**。合并脚本只读 `RAW = data/raw` 与 `data/feedback_training`，硬链接/复制到 `OUT = data/combined`，源数据集物理只读。
- `pending/rejected` 在第 3 步就被 `write_feedback_dataset()` 过滤，未审核纠偏根本进不了训练集，更不可能污染模型。

> 一句话：纠偏 → 审核（confirmed）→ 生成 YOLO → 幂等并入 combined → 复训；源数据只读、重复跳过、未审核不进，闭环安全。

## 模型评测基线

评测分两个口径，互补参照：

- **训练验证集 mAP**（整体）：复训时从 `results.csv` best epoch 写入 `model_registry`，管理端「模型版本注册」区展示，口径偏乐观，用于版本粗筛；
- **独立测试集 P/R/F1**（逐类）：`evaluate_models.py` 在测试集上逐图推理，写入 `data/eval/model_eval.json`，管理端「模型评估摘要」表展示，暴露单类弱点、是优化方向的依据。

```bash
# 评测所有已注册版本（默认）
.\.venv313\Scripts\python.exe scripts/evaluate_models.py --thresholds 0.25 0.30 0.35 0.45
# 只评测指定版本
.\.venv313\Scripts\python.exe scripts/evaluate_models.py --version v3
```

模型路径从 `model_registry` 读取（不再硬编码），按「场景+版本」聚合 merge 写入（不覆盖已有版本）。新增模型后跑一次本脚本即可补入独立测试集评测，无需改代码。实测基线（最佳阈值，独立测试集）：

**火情**（55 张测试图）：

| 版本 | 类别 | 最佳阈值 | Precision | Recall | F1 |
| --- | --- | ---: | ---: | ---: | ---: |
| v2（当前活跃） | 烟雾 | 0.35 | 0.91 | 0.91 | 0.91 |
| v2（当前活跃） | 火花 | 0.35 | 0.82 | 0.77 | 0.79 |
| v3（复训失败，未启用） | 烟雾 | — | 0.00 | 0.00 | 0.00 |
| v3（复训失败，未启用） | 火花 | — | 0.00 | 0.00 | 0.00 |

**PPE**（90 张测试图）：

| 版本 | 类别 | 最佳阈值 | Precision | Recall | F1 |
| --- | --- | ---: | ---: | ---: | ---: |
| v2 | 佩戴安全帽 | 0.25 | 0.82 | 0.82 | 0.82 |
| v2 | 未戴安全帽 | 0.25 | 0.80 | 0.67 | 0.73 |
| v2 | 未穿反光衣 | 0.25 | 0.80 | 0.67 | 0.73 |
| v2 | 穿着反光衣 | 0.25 | 0.91 | 0.78 | 0.84 |
| v2 | 人员 | 0.25 | 0.94 | 0.21 | 0.35 |
| v3（当前活跃） | 佩戴安全帽 | 0.25 | 0.87 | 0.87 | 0.87 |
| v3（当前活跃） | 未戴安全帽 | 0.25 | 0.88 | 0.63 | 0.73 |
| v3（当前活跃） | 未穿反光衣 | 0.25 | 0.89 | 0.66 | 0.76 |
| v3（当前活跃） | 穿着反光衣 | 0.25 | 0.91 | 0.78 | 0.84 |
| v3（当前活跃） | 人员 | 0.25 | 0.90 | 0.47 | 0.62 |

结论：
- 火情 v2 在 0.30–0.35 阈值 F1 最佳（已在 config 落地）；v3 复训严重退化（独立测试集 TP=0，训练 mAP50 仅 0.11），已保持 v2 活跃、未切 v3。
- PPE v3 逐类全面优于 v2，尤其 `person` 召回 0.21→0.47、`佩戴安全帽` F1 0.82→0.87，已切换为活跃版本。
- 仍存短板：`no_helmet`/`no_vest` 召回不足 0.70、`person` 召回偏低（人员框偏少），是后续数据补采与重训的优先项。

## 端到端评测指标

`scripts/eval_metrics.py` 实测（CPU，单帧/单次查询）：

| 维度 | 指标 | 值 | 说明 |
| --- | --- | ---: | --- |
| 检测延迟 | 火情头单帧 | ~318 ms | YOLOv8s ONNX，CPU |
| 检测延迟 | PPE 头单帧 | ~883 ms | YOLOv8 ONNX，CPU |
| 检测延迟 | 双头并行 | ~968 ms | `ThreadPoolExecutor`，取最大而非求和 |
| 检测延迟 | 双头串行 | ~1201 ms | 对比基线，并行省约 20% |
| RAG | 知识库条目 | 22 块 | 动火作业规范切分 |
| RAG | 召回率@5 | 1.0 | chunk 派生查询（上界） |
| RAG | 平均查询延迟 | ~78 ms | BGE encode + Chroma cosine |
| RAG | 领域查询 top1 | 0.54–0.84 | 「监火人」0.84 / 「可燃气体」0.54 |

检测 mAP 见答辩材料与管理端「模型版本注册」（火情 v2 mAP50=0.898、PPE v3 mAP50=0.695，训练验证集）；独立测试集逐类 P/R/F1 见上表与管理端「模型评估摘要」。实时链路双头并行后单帧 ~0.97s，满足「采样节奏 ≠ 告警 SLA」下的连续监测；RAG 查询百毫秒级，不阻塞告警（条款异步回填）。

## 后续优化计划

> 由于本次开发周期有限，且硬件设备条件存在一定限制，项目完成度尚有提升空间，仍存在不少待改进的问题。基于现有成果，规划未来可开展的优化工作如下：

### 检测模型与 mAP
- 当前 `no_helmet`（未戴安全帽）、`no_vest`（未穿反光衣）、`person`召回偏低（见评测基线），是数据补采与重训的优先项；
- 阈值与 NMS 调参空间尚存，`spark` 低置信光斑误报过滤阈值（`fp_filter.spark_conf_min`）可按现场继续收敛；
- 人工纠偏确认样本经 feedback 闭环周期性回写微调，提升长尾/夜间/逆光等场景召回。

### 新场景检测头扩展
- 当前为动火作业 + 施工 PPE 双场景，后续按建筑施工「五大伤害」逐步扩展检测头：
  - 高处坠落：安全带（系/未系）+ 临边洞口防护栏杆/盖板在位判定；
  - 临时用电/触电：裸露线缆、违规配电箱、电线拖地涉水；
  - 吸烟/明火源：施工区禁烟 + 易燃物共存；
- 动火子场景补齐 `face_shield`（防护面罩）与 `flammable`（易燃物）检测头——两者已声明接口，仅缺权重，补齐即闭合动火合规链路；
- 复用现有训练流水线 `prepare_combined_dataset → train_combined → export → register → switch`，每新增一场景对应一份 `scenes.<scene>` + `config/rules/<scene>.yaml` + 知识库集合。

### 链路与工程闭环
- **上传研判异步化**：引入后台执行器 + 进度轮询，主线程提交即返回，进度从「事后轨迹」变为「真·实时」；

### RAG 知识库与文档解析
- 当前 RAG 仅接受**文本型 PDF**（`core/pdf_parser.py` 经 PyMuPDF `get_text` 抽取，无 OCR），扫描件/图片型 PDF 抽出为空、无法入库；
- 扩展加 **OCR 预处理层**（PaddleOCR 或 Tesseract）先对扫描件做文字识别，再走现有条款切分 + 向量入库流水线；
- 扩展**格式归一化层**：`docx`/`doc`/`txt`/`md` 等非 PDF 文档统一转纯文本后入向量库，`import_pdf` 接口泛化为 `import_doc`；

### 其他
- **命名收口**：项目定名「海之子 · 动火作业安全智能体」，施工 PPE 作为第二场景并存；随场景扩展可往「建筑施工安全」收敛，动火作为核心子场景；
- **RTSP 采样与告警 SLA**：火情关键源 `monitor.interval_sec` 建议 ≤3s，必要时改连续抓帧入队模型，明确「采样节奏 ≠ 告警 SLA」。

确认样本经 feedback 闭环周期性回写微调，提升长尾/夜间/逆光等场景召回。


## 部署注意

- `.gitignore` 已忽略：`data/raw/`、`data/combined/`、`data/eval/`、`data/feedback_training/`、`data/runs_combined/`、`data/kb/*.pdf`、`data/uploads/`、`data/exports/`、`data/app.db*`、`__pycache__/`、`*.pyc`、`.venv313/`、`plugins/`、官方 `yolov8n.pt`/`yolov8s.pt` 及 `data/models/BAAI--bge-small-zh-v1.5/`、`data/models/Qwen2.5-0.5B-Instruct/`。
- **需自行训练**：小模型推理权重（data/models/*.onnx，约 42MB/个）不随仓库提交，赛前打包另附。训练入口 scripts/train_combined.py（合并集训练）/ scripts/train_ppe_local.py + scripts/export_ppe_onnx.py（PPE 本地训练+导出），管理端复训按钮亦可。
- **需另行获取**：BGE Embedding 模型（约 100MB，`python scripts/setup_models.py` 下载）；原始数据集（`data/raw/`，仅复现训练/评测需要）。
- 首次启动自动建库 + 种子默认账号（`core/bootstrap.py`）；知识库向量库 data/kb/chroma/ 不进 git（赛前打包另附），查询仍需 BGE 模型，故 `setup_models.py` 为必跑步骤。实时监测态不依赖知识库。
- LLM 润色走 ollama `qwen3:8b`（独立进程，异步润色不进主链路）；app 启动后台预热一次 + 每次 `keep_alive=30m` 常驻，规避 5.2GB 模型反复冷启；断网/不可用自动降级为模板工单。

## 技术栈

- **前端**：Streamlit（原生 `st.navigation` 多页路由）
- **视觉推理**：ONNX Runtime（YOLOv8）
- **规范检索**：sentence-transformers（BGE-small-zh）+ ChromaDB
- **本地 LLM**：Ollama `qwen3:8b`（可选增强，断网降级）
- **持久化**：SQLite
- **工程化**：Docker / docker-compose、GitHub Actions CI、PowerShell 统一脚本

## 说明

本项目面向教学/演示场景，识别能力以本地 ONNX 权重为准。PPE 头按 `construction-safety-gsnvb` / industrial-safety-vision 工程路线训练导出，类名须与 `config.yaml` 中 `class_map` 完全一致。`scripts/train_ppe_local.py` 与 `scripts/export_ppe_onnx.py` 为本地训练/导出入口。
