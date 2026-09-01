# 智护工地 · 施工安全智能体

> **建筑安全领域的多模态智能体**：眼睛（YOLO 视觉感知——图片 / 视频 / 实时摄像头）→ 大脑（受限 Plan-and-Execute 认知层，LLM 只做规划与汇总）→ 手脚（6 个注册工具，嵌在派发→整改→验收的完整工单闭环里）→ 边界（风险定级规则查表、副作用强制人工确认）。覆盖**动火作业**与**施工 PPE** 双场景；前后端分离架构：React 前端 + FastAPI 接口层（AI 助手 + 影像研判双窗口），Streamlit 保留为回退入口。云端大模型为认知层默认通道，本地 Ollama 兜底；模型推理（YOLO/BGE/ASR）与数据链路全程本地化，断链自动降级。

[![Quality gate status](https://sonarcloud.io/api/project_badges/measure?project=duk-destiny_Smart-Construction-Site-Guardian&metric=alert_status&token=820ed34b7f80191064245ea5090a00e98eb45623)](https://sonarcloud.io/summary/new_code?id=duk-destiny_Smart-Construction-Site-Guardian)[![Maintainability Rating](https://sonarcloud.io/api/project_badges/measure?project=duk-destiny_Smart-Construction-Site-Guardian&metric=sqale_rating&token=820ed34b7f80191064245ea5090a00e98eb45623)](https://sonarcloud.io/summary/new_code?id=duk-destiny_Smart-Construction-Site-Guardian)[![Lines of Code](https://sonarcloud.io/api/project_badges/measure?project=duk-destiny_Smart-Construction-Site-Guardian&metric=ncloc&token=820ed34b7f80191064245ea5090a00e98eb45623)](https://sonarcloud.io/summary/new_code?id=duk-destiny_Smart-Construction-Site-Guardian)

**当前版本 v2.2**（2026-08-30），变更史见 [CHANGELOG.md](CHANGELOG.md)。

## 它能做什么

**一双眼睛（多模态感知）**
- **影像研判窗口**：取证图片/视频上传 → 五段流水线（`pipeline/`，`*Stage` 确定性组件）视觉 ∥ 规范 → 融合 → 复核 → 处置，RAG 条款引用、证据链落库可回放；
- **实时监测**：RTSP 摄像头流 WebSocket 帧广播，首帧 critical 当帧出红框 + 声音告警 + Webhook 推送。

**一个大脑（受限 Plan-and-Execute 认知层）**
- **AI 助手窗口**：类豆包对话窗——会话管理 / 上下文与跨会话记忆 / 影像附件分析 / 语音输入 / 工具抽屉（文字建单·影像分析·周报生成·快捷查询）；LLM 只在「规划 ≤8 步 / 汇总」出场，每步入证据链。

**一双手脚（嵌在业务闭环里的工具）**
- **工单闭环**：派发 → 整改 → 验收/驳回 → 逾期催办越级，五类动作全落审计；责任人工单内可直接「问 AI」（仅本单上下文只读问询）；
- **风险周报**：三源确定性聚合，数字强制溯源工具统计，fpdf2 中文 PDF 在线预览下载；
- **人工纠偏闭环**：改判/逐目标纠偏 → 审核 → 生成训练数据 → 复训回写。

**一条边界（人机协同）**
- 风险定级规则查表、LLM 永不进定级；副作用强制人工确认、无豁免开关；判定链路零依赖 LLM，断网可控衰减不瘫痪。

三级合规（红/黄/绿）规则数据驱动，可不改代码调整。

## 文档索引

| 编号 | 文档 | 内容 |
| --- | --- | --- |
| 01 | [快速开始](docs/01_快速开始.md) | 安装、两套入口启动、默认账号、Docker、CI |
| 02 | [系统架构](docs/02_系统架构.md) | 双链路架构、认知层、目录结构、技术栈 |
| 03 | [功能与页面](docs/03_功能与页面.md) | 页面与角色、检测能力、研判流水线五段职责、工单/周报/纠偏/告警/模型版本 |
| 04 | [配置说明](docs/04_配置说明.md) | config.yaml 全部配置段 |
| 05 | [API 接口](docs/05_API接口.md) | FastAPI 资源路由、认证、实时 Hub、WebSocket |
| 06 | [测试与评测](docs/06_测试与评测.md) | 测试体系、模型评测基线、端到端性能指标 |
| 07 | [部署与生产安全](docs/07_部署与生产安全.md) | 开箱即用说明、生产部署安全清单 |
| 08 | [设计取舍 QA](docs/08_设计取舍QA.md) | 「为什么这么设计」十个经典问题 |
| 09 | [后续优化计划](docs/09_后续优化计划.md) | 模型/场景/链路/知识库待改进项 |

## 最短启动路径

```bash
# Windows 一键（自动建虚拟环境、装依赖、启动）
./run_app.ps1

# 或手动：
cp config/config.example.yaml config/config.yaml   # fresh clone 必须
pip install -r requirements.txt                     # 推荐 Python 3.13
python -m uvicorn api.main:app --port 8000          # React 前端 + API 单端口
# 浏览器打开 http://localhost:8000
```

首次启动自动建库并种子默认账号（`core/bootstrap.py`，幂等）：

| 账号 | 密码 | 角色 | 可见页面 |
| --- | --- | --- | --- |
| `admin` | `admin123` | 管理员 | 全部页面 + 管理端 |
| `safety` | `demo1234` | 安全员 | 上报/研判/工单/历史/实时 |
| `lisi` | `demo1234` | 整改责任人 | 仅「我的整改单」 |

种子账号带「初始密码未改」标记，登录后可改密；生产建议开启强制改密（见 [07 部署与生产安全](docs/07_部署与生产安全.md)）。

## 技术栈

React 18 + Vite + AntD（主推前端）· FastAPI（接口层 + 静态托管）· Streamlit（回退入口）· ONNX Runtime（YOLOv8 推理）· BGE-small-zh + ChromaDB（本地 RAG）· 云端 LLM API + 本地 Ollama `qwen3:8b`（认知层四级降级链）· SQLite（WAL）· Docker / GitHub Actions CI。

## 说明

本项目仅供教学与演示使用，识别效果以本地 ONNX 权重运行结果为准。模型输出类别名称必须和 `config/config.yaml` 的 class_map 映射表完全匹配。版权声明：未经作者许可，禁止商用；禁止未经授权直接将本项目（含模型、代码）用于竞赛参赛，违者必究。
