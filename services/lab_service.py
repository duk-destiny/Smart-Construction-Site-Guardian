"""Agent 测试场门面（Phase 0）：page_lab 的干跑试跑下沉到服务层。

干跑不入库铁律在此保证：直接调用 agents，无 DAO、无 save_result、无审计；
页面只负责渲染返回的 dict。
"""
from __future__ import annotations

from agents.action_agent import ActionAgent
from agents.base import AgentMessage
from agents.fusion_agent import FusionAgent
from agents.rule_agent import RuleAgent
from agents.vision_agent import VisionAgent
from core.config import shared_config
from core.rag_engine import RagEngine
from services.enhance_service import EnhanceEngine


def lab_task_id() -> str:
    import uuid
    return f"lab_{uuid.uuid4().hex[:8]}"


def agent_status(msg) -> dict:
    """AgentMessage → 可序列化摘要（状态行 + payload）。"""
    return {"status": msg.status, "cost_ms": msg.cost_ms,
            "error": msg.error, "payload": msg.payload}


def run_vision(scene_id: str, image_path: str) -> dict:
    msg = VisionAgent(scene_id=scene_id).run(
        __import__("agents.base", fromlist=["AgentMessage"]).AgentMessage(
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
    msg = RuleAgent(rag=rag).run(AgentMessage(
        task_id=lab_task_id(), agent="rule", status="pending",
        payload={"permit_info": permit_info,
                 "violation_descs": violation_descs,
                 "skip_rag": False}))
    return agent_status(msg)


def run_fusion(scene_id: str, detections: list[dict],
               compliance: list[dict]) -> dict:
    msg = FusionAgent(scene_id=scene_id).run(AgentMessage(
        task_id=lab_task_id(), agent="fusion", status="pending",
        payload={"detections": detections, "compliance": compliance}))
    return agent_status(msg)


def template_notice(hazard: str, clause: str, risk_level: str) -> str:
    """处置 Agent 模板话术（确定性主链路文案）。"""
    return ActionAgent(work_order_dao=None)._template(hazard, clause, risk_level)


def polish_with(provider_name: str, hazard: str, clause: str,
                risk_level: str) -> tuple[str | None, str | None]:
    """按指定 base 润色（仅展示对比；主链路润色走 LlmEngine 异步链）。"""
    eng = EnhanceEngine()
    out = eng.chat(
        provider_name,
        "你是工地安全提醒助手。用一线工人听得懂的大白话输出整改提示，"
        "只依据给定信息组织语言，不得编造法规名称或条款编号。",
        f"隐患：{hazard}；规范依据：{clause}；风险等级：{risk_level}。"
        "请输出一段提醒工人的整改提示。",
        num_predict=220)
    return out, eng.last_error


def run_chain(scene_id: str, image_path: str | None,
              permit_info: dict) -> dict:
    """整链干跑：同一 Orchestrator，不传 work_order_dao、不落库。"""
    from agents.orchestrator import Orchestrator
    orch = Orchestrator(scene_id=scene_id)
    result = orch.execute(lab_task_id(),
                          images=[image_path] if image_path else [],
                          permit_info=permit_info)
    return result.to_dict()


def provider_names() -> list[str]:
    """可选 LLM base 名单（enhance.providers）。"""
    return [p["name"] for p in EnhanceEngine().providers]
