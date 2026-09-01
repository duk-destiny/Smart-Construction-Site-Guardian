"""影像研判五段流水线（run_video_pipeline 工具的内部实现单元）。

视觉检测 → 规范检索 → 融合定级 → 复核 → 处置，由 Orchestrator 编排，
总墙钟预算 ≤8s、超时逐级降级；主链路全确定性（LLM 仅复核段旁路辅助
意见与处置段异步润色，均不参与风险定级）。

命名注意：本包各段是确定性管线组件（*Stage），不是 LLM 智能体——
系统的顶层智能体是 services/agent/ 认知层（Plan-and-Execute 内核），
本包经该层的 run_video_pipeline 工具被调用（v2.1 前曾为独立 Agent 链，
v2.1 起整体降级为工具层内部实现）。
"""
