# 海之子 · 动火作业安全多 Agent 智能体

面向动火作业与施工 PPE 场景的本地化安全智能体：支持**上传研判**（多 Agent 重链路）与**实时摄像头监测**（轻链路）双模式，全程零外网依赖，可离线部署。

当前已追加：复核 Agent、证据链输入/输出摘要、人工纠偏反馈闭环、可视化逐目标纠偏工作台、连续帧目标跟踪、告警生命周期、服务层权限校验、管理端复训任务/日志轮询/早停保存/自动注册/新旧对比切换、模型版本注册/评估/回滚 UI、场景阈值配置、防护装备矛盾框过滤和统一测试/启动脚本。

## 功能概览

| 模式 | 页面 | 能力 |
| --- | --- | --- |
| 上传研判 | 上传与作业票 / 多 Agent 研判 / 工单改判导出 | 图片/视频 → 视觉 Agent + 规范 Agent(RAG) + 风险融合 + 闭环工单 |
| 实时监测 | 实时摄像头监测 | 摄像头帧 / 多路 RTSP 源 → 双场景检测头联合检测 → 三级合规 + 红框高亮 + 800Hz 声音警报 + Toast |
| 追溯分析 | 检测历史与分析 | 检测记录追踪、合规率趋势(柱状图)、隐患类别分布、日期筛选、CSV 导出 |

### 双场景检测头（复用现有权重，实时态同时接入）

- **动火作业安全 `hot_work`**：火花/烟雾/灭火器（YOLOv8s ONNX，`yolov8_fire_smoke_v2.onnx`），推荐阈值 `0.30-0.35`。
- **施工 PPE / 危险检测 `construction_ppe`**：安全帽/反光衣/人员（PPE 头，`ppe_yolov8_v2.onnx`）+ 堆放物倾斜检测（Detecting-danger 独门能力，OpenCV DNN YOLOv3 `yolov3-personload.*`），推荐阈值 `0.25`。

### 三级合规（B1）

| 级别 | 颜色 | 含义 |
| --- | --- | --- |
| 不合规 | 红 | 即时高危（火花/烟雾/未戴安全帽/堆放物倾斜等），红框高亮 + 声音警报 |
| 警告 | 黄 | 需关注（易燃物/未穿反光衣/堆放物等） |
| 合规 | 绿 | 无违规目标或仅检出安全信号（已佩戴安全帽/反光衣/人员在场） |

分级规则数据驱动，见 `config/config.yaml` 的 `compliance.severity`，可在不改代码的前提下调整。

### Agent 运行证据链

每次上传研判会把视觉/规范/融合/复核/处置 Agent 的输入摘要、输出摘要、状态、耗时和异常信息写入 `agent_runs` 表，
供多 Agent 协同追溯、人工改判核验和答辩演示使用。规则 Agent 在并行预检阶段只检查作业票，
拿到视觉证据后再补一次 RAG 条款检索，避免同一任务重复跑完整规范链路。

### 人工纠偏闭环

- 安全员改判或逐目标纠偏后自动写入 `feedback_samples`；
- 管理端可审核 `pending / confirmed / rejected`；
- `scripts/build_feedback_dataset.py` / `scripts/prepare_feedback_training.py` 可将确认样本生成场景级 YOLO 候选训练数据；
- `scripts/export_feedback.py` 可导出 CSV。

### 告警外部推送（P0/P1）

- 高危帧自动创建告警事件 → 现场标注帧截图留存（`data/alarms/`）→ 异步推送到企业微信/钉钉/通用 Webhook；
- 推送结果写 `notification_logs` 留痕（sent / failed / skipped），管理端可测试推送并追溯；
- 支持多路 RTSP 后台自动轮询（`monitor.*` 配置），按 `(source, cls)` 冷却去重，持续违规可周期性告警；
- 默认关闭，配置见 `config/config.yaml` 的 `notify.*` / `monitor.*`，详见 `docs/notify-flow.md`。

### 告警生命周期

- 实时高危帧自动创建告警事件；
- 支持新告警/已确认/误报/已处理状态流转；
- 同一会话同一类别短时间重复告警自动去重。

## 目录结构

```
hzz-fire-safety/
├── app.py                  # Streamlit 入口（NAV 路由 + 全局主题）
├── run_tests.ps1           # 统一全量测试
├── run_app.ps1             # 统一应用启动
├── Dockerfile / docker-compose.yml  # 容器化部署
├── .github/workflows/ci.yml         # 自动测试
├── config/
│   ├── config.yaml         # 全局与多场景配置（权重路径/类别映射/三级合规）
│   └── rules/              # 各场景风险融合矩阵（hot_work / construction_ppe）
├── core/                   # 最底层：推理引擎与工具
│   ├── yolo_engine.py      # YOLOv8 ONNX 推理（文件/帧两种输入）
│   ├── load_object_detector.py  # 堆放物检测 + Hough 倾斜判定（帧输入支持）
│   ├── compliance.py       # 三级合规研判（数据驱动）
│   ├── realtime_engine.py  # 实时轻链路：多场景头联合检测 + 绘图
│   ├── config.py / rag_engine.py / video_utils.py / yolo_adapter.py
├── agents/                 # 多 Agent 编排（视觉/规范/融合/复核/处置）
├── services/ dao/          # 业务服务与 SQLite 持久化
├── ui/                     # 页面（upload/realtime/agents/report/history/admin/login/theme）
├── scripts/                # 数据准备、训练、评测、纠偏、模型注册脚本
├── docs/
│   ├── specs/              # P0-P6 阶段规格
│   ├── development-complete.md
│   └── defense-materials.md
├── data/
│   ├── models/             # 推理权重（v2 ONNX；BAAI/raw 已忽略）
│   ├── kb/ uploads/ exports/  # 运行期生成目录（多为 .gitignore 忽略）
│   └── app.db              # SQLite（运行期生成）
└── tests/                  # 测试
```

## 运行

```bash
# 依赖（离线优先，见 requirements.txt）
pip install -r requirements.txt

# 启动（默认监听 0.0.0.0:8501）
.\run_app.ps1

# 全量测试
.\run_tests.ps1
```

实时摄像头页面使用 `st.camera_input` 零依赖轮询方案：点击捕获帧即检测并展示，开启"连续监控"后自动刷新等待下一帧；**声音警报仅在实时监测态、且不合规时触发**。页面同时支持多路 RTSP / 本地视频源按帧抓取。

## Docker / CI

```bash
# 构建并启动容器（默认 8501）
docker compose up --build

# 容器内运行全量测试
docker compose run --rm app python -m pytest tests -q --tb=short -p no:cacheprovider
```

`.github/workflows/ci.yml` 会在 push / pull request 时于 Ubuntu 上安装中文字体并运行全量测试。

> 构建说明：Dockerfile 使用 `python:3.13-slim-bookworm`，并将 apt 源切到 `mirrors.aliyun.com`，避免 `deb.debian.org` 在当前网络环境不可用导致构建失败。

## PPE / 检测权重来源与训练

- 识别能力以本地 ONNX 权重为准。当前入库权重：`yolov8_fire_smoke_v2.onnx`（火情）、`ppe_yolov8_v2.onnx`（PPE）、`yolov3-personload.*`（堆放物）。
- PPE 头按 `construction-safety-gsnvb` / industrial-safety-vision 工程路线训练导出；类名须与 `config.yaml` 中 `class_map` 完全一致。`scripts/train_ppe_local.py` 与 `scripts/export_ppe_onnx.py` 为本地训练/导出入口。
- 白名单（`core/yolo_engine.py` 的 `WHITELIST`/`WHITELIST_CN`）共 12 类，其中 `face_shield`/`extinguisher`/`flammable` 为规范侧占位（模型未直接输出时由规范 Agent 结合人工核查判定），其余类别由检测头直接支撑。

## 模型评测基线

```bash
.\.venv313\Scripts\python.exe scripts\evaluate_models.py --thresholds 0.25 0.30 0.35 0.45
```

脚本会在火情与 PPE 测试集上计算各隐患类别的 Precision/Recall/F1，并把结果写入
`data/eval/model_eval.json`。当前基线显示 `no_helmet`（未戴安全帽）和 `no_vest`
（未穿反光衣）召回率偏低，是后续数据补采和重训的优先项。

## 部署注意（提交/上传比赛平台前）

- `.gitignore` 已忽略：`data/raw/`、`data/combined/`、`data/eval/`、`data/feedback_training/`、`data/runs_combined/`、`data/kb/chroma/`、`data/kb/*.pdf`、`data/uploads/`、`data/exports/`、`data/app.db*`、`__pycache__/`、`*.pyc`、`.venv313/`、`plugins/`、官方 `yolov8n.pt`/`yolov8s.pt` 及 `data/models/BAAI--bge-small-zh-v1.5/`。
- 仅小体积推理权重（`.onnx`/`.weights`/`.cfg`/`.names`）纳入版本库；大模型 Embedding（`BAAI--bge-small-zh-v1.5`）与原始数据集需另行分发，勿入库。
- 知识库（RAG）需联网或本地模型首次构建后离线可用；实时监测态不依赖知识库。
