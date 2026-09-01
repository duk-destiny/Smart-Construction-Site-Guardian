"""Agent 测试场门面（Phase 0）：page_lab 的干跑试跑下沉到服务层。

干跑不入库铁律在此保证：直接调用 agents，无 DAO、无 save_result、无审计；
页面只负责渲染返回的 dict。
"""
from __future__ import annotations

from pipeline.action import ActionStage
from pipeline.base import StageMessage
from pipeline.fusion import FusionStage
from pipeline.rule import RuleStage
from pipeline.vision import VisionStage
from core.config import shared_config
from core.rag_engine import RagEngine
from services.enhance_service import EnhanceEngine


def lab_task_id() -> str:
    import uuid
    return f"lab_{uuid.uuid4().hex[:8]}"


def agent_status(msg) -> dict:
    """StageMessage → 可序列化摘要（状态行 + payload）。"""
    return {"status": msg.status, "cost_ms": msg.cost_ms,
            "error": msg.error, "payload": msg.payload}


def run_vision(scene_id: str, image_path: str) -> dict:
    msg = VisionStage(scene_id=scene_id).run(
        __import__("pipeline.base", fromlist=["StageMessage"]).StageMessage(
            task_id=lab_task_id(), agent="vision", status="pending",
            payload={"image_paths": [image_path]}))
    return agent_status(msg)


def run_rule(scene_id: str, permit_info: dict,
             violation_descs: list[str]) -> dict:
    kb = None
    try:
        kb = shared_config().get_scene(scene_id).get("kb_collection")
    except Exception:  # noqa: BLE001 场景缺 KB 配置时走默认集合
        kb = None
    rag = RagEngine(collection_name=kb) if kb else RagEngine()
    msg = RuleStage(rag=rag).run(StageMessage(
        task_id=lab_task_id(), agent="rule", status="pending",
        payload={"permit_info": permit_info,
                 "violation_descs": violation_descs,
                 "skip_rag": False}))
    return agent_status(msg)


def run_fusion(scene_id: str, detections: list[dict],
               compliance: list[dict]) -> dict:
    msg = FusionStage(scene_id=scene_id).run(StageMessage(
        task_id=lab_task_id(), agent="fusion", status="pending",
        payload={"detections": detections, "compliance": compliance}))
    return agent_status(msg)


def template_notice(hazard: str, clause: str, risk_level: str) -> str:
    """处置 Agent 模板话术（确定性主链路文案）。"""
    return ActionStage(work_order_dao=None)._template(hazard, clause, risk_level)


def polish_with(provider_name: str, hazard: str, clause: str,
                risk_level: str) -> tuple[str | None, str | None]:
    """按指定 base 润色（仅展示对比）：经统一 ChatClient 显式指定 provider，
    返回 (文本或 None, 错误信息或 None)；对比语义保留（v2.1 §10 连带迁移）。"""
    from core.chat_client import get_chat_client
    result = get_chat_client().chat(
        "你是工地安全提醒助手。用一线工人听得懂的大白话输出整改提示，"
        "只依据给定信息组织语言，不得编造法规名称或条款编号。",
        f"隐患：{hazard}；规范依据：{clause}；风险等级：{risk_level}。"
        "请输出一段提醒工人的整改提示。",
        max_tokens=220, total_deadline_sec=30.0,
        provider=provider_name)
    if result.status == "failed":
        return None, result.error or "调用失败"
    if not isinstance(result.content, str):
        return None, f"[{provider_name}] 非文本输出"
    return result.content.strip() or None, None


def run_chain(scene_id: str, image_path: str | None,
              permit_info: dict) -> dict:
    """整链干跑：同一 Orchestrator，不传 work_order_dao、不落库。"""
    from pipeline.orchestrator import Orchestrator
    orch = Orchestrator(scene_id=scene_id)
    result = orch.execute(lab_task_id(),
                          images=[image_path] if image_path else [],
                          permit_info=permit_info)
    return result.to_dict()


def provider_names() -> list[str]:
    """可选 LLM base 名单（enhance.providers）。"""
    return [p["name"] for p in EnhanceEngine().providers]
