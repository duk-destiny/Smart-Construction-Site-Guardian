# 海之子 · 动火作业安全多 Agent 智能体

面向动火作业与施工 PPE 场景的本地化安全智能体：支持**上传研判**（多 Agent 重链路）与**实时摄像头监测**（轻链路）双模式，全程零外网依赖，可离线部署。

## 功能概览

| 模式 | 页面 | 能力 |
| --- | --- | --- |
| 上传研判 | 上传与作业票 / 多 Agent 研判 / 工单改判导出 | 图片/视频 → 视觉 Agent + 规范 Agent(RAG) + 风险融合 + 闭环工单 |
| 实时监测 | 实时摄像头监测 | 摄像头帧 → 双场景检测头联合检测 → 三级合规 + 红框高亮 + 800Hz 声音警报 + Toast |
| 追溯分析 | 检测历史与分析 | 检测记录追踪、合规率趋势(柱状图)、隐患类别分布、日期筛选、CSV 导出 |

### 双场景检测头（复用现有权重，实时态同时接入）

- **动火作业安全 `hot_work`**：火情/火花/烟雾（YOLOv8 ONNX，`yolov8_fire_smoke.onnx`）。
- **施工 PPE / 危险检测 `construction_ppe`**：安全帽/反光衣（PPE 头，`ppe_yolov8.onnx`）+ 堆放物倾斜检测（Detecting-danger 独门能力，OpenCV DNN YOLOv3 `yolov3-personload.*`）。

### 三级合规（B1）

| 级别 | 颜色 | 含义 |
| --- | --- | --- |
| 不合规 | 红 | 即时高危（火花/烟雾/未戴安全帽/堆放物倾斜等），红框高亮 + 声音警报 |
| 警告 | 黄 | 需关注（易燃物/未穿反光衣/堆放物等） |
| 合规 | 绿 | 无违规目标或仅检出安全信号（已佩戴安全帽/反光衣/人员在场） |

分级规则数据驱动，见 `config/config.yaml` 的 `compliance.severity`，可在不改代码的前提下调整。

## 目录结构

```
hzz-fire-safety/
├── app.py                  # Streamlit 入口（NAV 路由 + 全局主题）
├── config/
│   ├── config.yaml         # 全局与多场景配置（权重路径/类别映射/三级合规）
│   └── rules/              # 各场景风险融合矩阵（hot_work / construction_ppe）
├── core/                   # 最底层：推理引擎与工具
│   ├── yolo_engine.py      # YOLOv8 ONNX 推理（文件/帧两种输入）
│   ├── load_object_detector.py  # 堆放物检测 + Hough 倾斜判定（帧输入支持）
│   ├── compliance.py       # 三级合规研判（数据驱动）
│   ├── realtime_engine.py  # 实时轻链路：多场景头联合检测 + 绘图
│   ├── config.py / rag_engine.py / video_utils.py / yolo_adapter.py
├── agents/                 # 多 Agent 编排（视觉/规范/融合/闭环）
├── services/ dao/          # 业务服务与 SQLite 持久化
├── ui/                     # 页面（upload/realtime/agents/report/history/admin/login/theme）
├── scripts/                # PPE 训练与 ONNX 导出脚本
├── data/
│   ├── models/             # 推理权重（已入库：ppe_yolov8.onnx 等；BAAI/raw 已忽略）
│   ├── kb/ uploads/ exports/  # 运行期生成目录（多为 .gitignore 忽略）
│   └── app.db              # SQLite（运行期生成）
└── tests/                  # 测试
```

## 运行

```bash
# 依赖（离线优先，见 requirements.txt）
pip install -r requirements.txt

# 启动（默认监听 0.0.0.0:8501）
streamlit run app.py --server.address 0.0.0.0 --server.port 8501
# 或直接： run.bat
```

实时摄像头页面使用 `st.camera_input` 零依赖轮询方案：点击捕获帧即检测并展示，开启"连续监控"后自动刷新等待下一帧；**声音警报仅在实时监测态、且不合规时触发**。

## PPE / 检测权重来源与训练

- 识别能力以本地 ONNX 权重为准。当前入库权重：`yolov8_fire_smoke.onnx`（火情）、`ppe_yolov8.onnx`（PPE）、`yolov3-personload.*`（堆放物）。
- PPE 头按 `construction-safety-gsnvb` / industrial-safety-vision 工程路线训练导出；类名须与 `config.yaml` 中 `class_map` 完全一致。`scripts/train_ppe_local.py` 与 `scripts/export_ppe_onnx.py` 为本地训练/导出入口。
- 白名单（`core/yolo_engine.py` 的 `WHITELIST`/`WHITELIST_CN`）共 12 类，其中 `face_shield`/`extinguisher`/`flammable` 为规范侧占位（模型未直接输出时由规范 Agent 结合人工核查判定），其余类别由检测头直接支撑。

## 部署注意（提交/上传比赛平台前）

- `.gitignore` 已忽略：`data/raw/`、`data/kb/chroma/`、`data/kb/*.pdf`、`data/uploads/`、`data/exports/`、`data/app.db*`、`__pycache__/`、`*.pyc`、`.venv/venv/venv313/`、`.idea/.vscode/` 及 `data/models/BAAI--bge-small-zh-v1.5/`。
- 仅小体积推理权重（`.onnx`/`.weights`/`.cfg`/`.names`）纳入版本库；大模型 Embedding（`BAAI--bge-small-zh-v1.5`）与原始数据集需另行分发，勿入库。
- 知识库（RAG）需联网或本地模型首次构建后离线可用；实时监测态不依赖知识库。
