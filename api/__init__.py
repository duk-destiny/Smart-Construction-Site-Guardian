"""FastAPI 接口层（Phase 2）：与 ui/ 平级，为移动端/第三方集成铺路。

分层约束（docs/前后端分离重构提示词.md Phase 2）：api 只 import services 层；
例外仅限 Phase 0 白名单同类只读依赖——core.config.shared_config（只读配置）
与 core.logging（日志）。router 内禁止业务逻辑与 SQL，只做入参校验、
服务调用与响应整形。
"""
