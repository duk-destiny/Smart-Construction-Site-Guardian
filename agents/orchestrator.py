"""总控编排器（M02）：并行编排 4 个契约化 Agent，超时降级，进度推送。

DAG：`[视觉 ∥ 规范] → 融合 → 闭环`。
- 视觉/规范 用 ThreadPoolExecutor 并行 submit，`future.result(timeout)` 精确超时
  （视觉 3s / 规范 2s），超时转 degraded（SRS 3.2.4）；
- 顶层 try/except 兜底，任一 Agent 崩溃标红（status=failed）不退出进程；
- 逐节点 progress_cb 推送进度供 UI 轮询。
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

from agents.action_agent import ActionAgent
from agents.base import AgentMessage
from agents.fusion_agent import FusionAgent
from agents.review_agent import ReviewAgent
from agents.rule_agent import RuleAgent
from agents.vision_agent import VisionAgent
from core.config import ConfigLoader
from core.rag_engine import RagEngine
from core.video_utils import VideoUtils

# 超时预算（秒，C3 主链路 ≤8s）
_TIMEOUT_VISION = 3.0
_TIMEOUT_RULE = 2.0


class Orchestrator:
    """多 Agent 并行编排器。"""

    def __init__(self, vision=None, rule=None, fusion=None, review=None, action=None,
                 progress_cb=None, scene_id: str | None = None):
        self._scene_id = scene_id
        # 视觉/融合 Agent 按场景加载检测头与风险矩阵
        self.vision = vision or VisionAgent(scene_id=scene_id)
        self.fusion = fusion or FusionAgent(scene_id=scene_id)
        # 规范 Agent 按场景加载对应知识库集合
        kb_collection = None
        if scene_id:
            try:
                kb_collection = ConfigLoader().get_scene(scene_id).get("kb_collection")
            except Exception:
                kb_collection = None
        self.rule = rule or RuleAgent(rag=RagEngine(collection_name=kb_collection)
                                     if kb_collection else RagEngine())
        self.review = review or ReviewAgent()
        self.action = action or ActionAgent()
        self._progress_cb = progress_cb or (lambda *a, **k: None)

    def _push(self, task_id: str, agent: str, status: str, cost_ms: int = 0) -> None:
        self._progress_cb(task_id, agent, status, cost_ms)

    @staticmethod
    def _safe(future, timeout: float, agent: str) -> AgentMessage:
        """取结果，超时/异常返回降级消息。"""
        try:
            return future.result(timeout=timeout)
        except FuturesTimeout:
            return AgentMessage(
                task_id="", agent=agent, status="degraded",
                payload={}, error=f"{agent} 超时({timeout}s)")
        except Exception as e:  # noqa: BLE001
            return AgentMessage(
                task_id="", agent=agent, status="failed",
                payload={}, error=f"{type(e).__name__}: {e}")

    def execute(self, task_id: str, images: list[str] | None = None,
                video: str | None = None, permit_info: dict | None = None) -> AgentMessage:
        """执行完整链路，返回闭环 Agent 的最终 AgentMessage。"""
        images = images or []
        permit_info = permit_info or {}
        self._push(task_id, "vision", "running")
        self._push(task_id, "rule", "running")

        # 视频抽帧并入图像列表（视觉阶段）
        frame_paths = []
        if video:
            try:
                frame_paths = VideoUtils.extract_frames(video)
                images = images + frame_paths
            except Exception:
                pass

        vmsg = AgentMessage(
            task_id=task_id, agent="vision", status="pending",
            payload={"image_paths": images}, error=None, cost_ms=0)
        rmsg = AgentMessage(
            task_id=task_id, agent="rule", status="pending",
            payload={"permit_info": permit_info, "violation_descs": [], "skip_rag": True},
            error=None, cost_ms=0)

        try:
            with ThreadPoolExecutor(max_workers=2) as ex:
                fv = ex.submit(self.vision.run, vmsg)
                fr = ex.submit(self.rule.run, rmsg)
                vout = self._safe(fv, _TIMEOUT_VISION, "vision")
                rout = self._safe(fr, _TIMEOUT_RULE, "rule")
        except Exception as e:  # noqa: BLE001 顶层兜底
            self._push(task_id, "vision", "failed")
            self._push(task_id, "rule", "failed")
            return AgentMessage(
                task_id=task_id, agent="orchestrator", status="failed",
                payload={}, error=f"{type(e).__name__}: {e}")

        self._push(task_id, "vision", vout.status, vout.cost_ms)
        self._push(task_id, "rule", rout.status, rout.cost_ms)

        # 视觉输出回灌规范 Agent：预检阶段只查作业票，拿到视觉证据后再补 RAG，
        # 避免同一任务重复执行一次完整的规范检索。
        violation_descs = vout.payload.get("violation_descs", []) if vout.status == "success" else []
        permit_noncompliant = any(
            c.get("verdict") == "不合规"
            for c in rout.payload.get("compliance", [])
        ) if rout.status == "success" else False
        if rout.status == "success" and (violation_descs or permit_noncompliant):
            self._push(task_id, "rule", "running")
            rmsg = AgentMessage(
                task_id=task_id, agent="rule", status="pending",
                payload={
                    "permit_info": permit_info,
                    "violation_descs": violation_descs,
                    "skip_rag": False,
                },
                error=None, cost_ms=0)
            rout = self.rule.run(rmsg)
            self._push(task_id, "rule", rout.status, rout.cost_ms)

        # 融合
        self._push(task_id, "fusion", "running")
        fmsg = self.fusion.run(AgentMessage(
            task_id=task_id, agent="fusion", status="pending",
            payload={
                "detections": vout.payload.get("detections", []) if vout.status == "success" else [],
                "compliance": rout.payload.get("compliance", []) if rout.status == "success" else [],
            }, error=None, cost_ms=0))
        self._push(task_id, "fusion", fmsg.status, fmsg.cost_ms)

        # 复核：高风险或证据不足的结果标记人工复核
        self._push(task_id, "review", "running")
        rvmsg = self.review.run(AgentMessage(
            task_id=task_id, agent="review", status="pending",
            payload={
                "detections": vout.payload.get("detections", []) if vout.status == "success" else [],
                "compliance": rout.payload.get("compliance", []) if rout.status == "success" else [],
                "risk_level": fmsg.payload.get("risk_level", "一般"),
                "filtered_fp": fmsg.payload.get("filtered_fp", []),
            }, error=None, cost_ms=0))
        self._push(task_id, "review", rvmsg.status, rvmsg.cost_ms)

        # 闭环处置
        self._push(task_id, "action", "running")
        amsg = self.action.run(AgentMessage(
            task_id=task_id, agent="action", status="pending",
            payload={
                "risk_level": fmsg.payload.get("risk_level", "一般"),
                "reasons": fmsg.payload.get("reasons", []),
                "compliance": rout.payload.get("compliance", []) if rout.status == "success" else [],
                "training_tips": rout.payload.get("training_tips", []) if rout.status == "success" else [],
                "needs_review": rvmsg.payload.get("needs_review", False),
                "review_reasons": rvmsg.payload.get("review_reasons", []),
            }, error=None, cost_ms=0))
        self._push(task_id, "action", amsg.status, amsg.cost_ms)

        # 整体降级/失败判定：failed > degraded > success
        failed = any(m.status == "failed" for m in (vout, rout, fmsg, rvmsg, amsg))
        degraded = any(m.status == "degraded" for m in (vout, rout, fmsg, rvmsg, amsg))
        if failed:
            overall = "failed"
        elif degraded:
            overall = "degraded"
        else:
            overall = "success"
        return AgentMessage(
            task_id=task_id, agent="orchestrator", status=overall,
            payload={
                "vision": {"status": vout.status, "payload": vout.payload},
                "rule": {"status": rout.status, "payload": rout.payload},
                "fusion": {"status": fmsg.status, "payload": fmsg.payload},
                "review": {"status": rvmsg.status, "payload": rvmsg.payload},
                "action": {"status": amsg.status, "payload": amsg.payload},
                "risk_level": fmsg.payload.get("risk_level"),
                "reasons": fmsg.payload.get("reasons"),
                "work_order": amsg.payload.get("work_order"),
                "worker_notice": amsg.payload.get("worker_notice"),
            }, error=None, cost_ms=0)
