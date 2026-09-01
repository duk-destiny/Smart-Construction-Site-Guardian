"""总控编排器（M02）：编排五段研判流水线，超时降级，进度推送。

DAG：`[视觉 ∥ 规范] → 融合 → 闭环`。
- 视觉/规范 用 ThreadPoolExecutor 并行 submit，`future.result(timeout)` 精确超时
  （视觉 3s / 规范 4s），超时转 degraded（SRS 3.2.4）；
- 顶层 try/except 兜底，任一段崩溃标红（status=failed）不退出进程；
- 逐节点 progress_cb 推送进度供 UI 轮询。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

from pipeline.action import ActionStage
from pipeline.base import StageMessage
from pipeline.fusion import FusionStage
from pipeline.review import ReviewStage
from pipeline.rule import RuleStage
from pipeline.vision import VisionStage
from core.config import ConfigLoader
from core.rag_engine import RagEngine
from core.video_utils import VideoUtils

# 超时预算（秒，C3 主链路 ≤8s）
_TIMEOUT_VISION = 3.0
_TIMEOUT_RULE = 4.0
_TIMEOUT_FUSION = 2.0
_TIMEOUT_REVIEW = 1.5
_TIMEOUT_ACTION = 1.5

# 执行模式（§5.10）：full=完整链路（默认，上传主链路零变化）；
# quick=跳过规范段二阶段 RAG 回灌，仅一阶段作业票检查（视频多轮追问提速）
VALID_MODES = ("full", "quick")


class Orchestrator:
    """影像研判五段流水线编排器。"""

    def __init__(self, vision=None, rule=None, fusion=None, review=None, action=None,
                 progress_cb=None, scene_id: str | None = None,
                 work_order_dao=None):
        self._scene_id = scene_id
        # 视觉/融合段按场景加载检测头与风险矩阵
        self.vision = vision or VisionStage(scene_id=scene_id)
        self.fusion = fusion or FusionStage(scene_id=scene_id)
        # 规范段按场景加载对应知识库集合
        kb_collection = None
        if scene_id:
            try:
                kb_collection = ConfigLoader().get_scene(scene_id).get("kb_collection")
            except Exception:
                kb_collection = None
        self.rule = rule or RuleStage(rag=RagEngine(collection_name=kb_collection)
                                     if kb_collection else RagEngine())
        self.review = review or ReviewStage()
        self.action = action or ActionStage(work_order_dao=work_order_dao)
        self._progress_cb = progress_cb or (lambda *a, **k: None)

    def _push(self, task_id: str, agent: str, status: str, cost_ms: int = 0) -> None:
        self._progress_cb(task_id, agent, status, cost_ms)

    @staticmethod
    def _run_with_timeout(fn, arg, timeout: float, agent: str, task_id: str = "") -> StageMessage:
        """在独立线程执行 fn(arg)，超时返回降级消息，不阻塞等待未完成线程。"""
        ex = ThreadPoolExecutor(max_workers=1)
        try:
            future = ex.submit(fn, arg)
            try:
                return future.result(timeout=timeout)
            except FuturesTimeout:
                return StageMessage(
                    task_id=task_id, agent=agent, status="degraded",
                    payload={}, error=f"{agent} 超时({timeout}s)")
            except Exception as e:  # noqa: BLE001
                return StageMessage(
                    task_id=task_id, agent=agent, status="failed",
                    payload={}, error=f"{type(e).__name__}: {e}")
        finally:
            ex.shutdown(wait=False, cancel_futures=True)

    @staticmethod
    def _safe(future, timeout: float, agent: str, task_id: str = "") -> StageMessage:
        """取结果，超时/异常返回降级消息（保留 task_id 便于追踪）。"""
        try:
            return future.result(timeout=timeout)
        except FuturesTimeout:
            return StageMessage(
                task_id=task_id, agent=agent, status="degraded",
                payload={}, error=f"{agent} 超时({timeout}s)")
        except Exception as e:  # noqa: BLE001
            return StageMessage(
                task_id=task_id, agent=agent, status="failed",
                payload={}, error=f"{type(e).__name__}: {e}")

    def execute(self, task_id: str, images: list[str] | None = None,
                video: str | None = None, permit_info: dict | None = None,
                mode: str = "full") -> StageMessage:
        """执行完整链路，返回末段处置的最终 StageMessage。

        mode（§5.10）：默认 "full"——既有调用方不传即完整链路，行为零变化；
        "quick" 跳过二阶段 RAG 回灌（保留一阶段作业票预检结论）。
        非法取值抛 ValueError（越界即拒，不静默降级）。
        """
        if mode not in VALID_MODES:
            raise ValueError(
                f"非法执行模式 {mode!r}，取值应为 {VALID_MODES}")
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
            except Exception as e:  # noqa: BLE001 抽帧失败按空影像降级，但留痕
                from core.logging import get_logger
                get_logger(__name__).warning(f"视频抽帧失败 {video}: {e}")

        vmsg = StageMessage(
            task_id=task_id, agent="vision", status="pending",
            payload={"image_paths": images}, error=None, cost_ms=0)
        rmsg = StageMessage(
            task_id=task_id, agent="rule", status="pending",
            payload={"permit_info": permit_info, "violation_descs": [], "skip_rag": True},
            error=None, cost_ms=0)

        try:
            with ThreadPoolExecutor(max_workers=2) as ex:
                fv = ex.submit(self.vision.run, vmsg)
                fr = ex.submit(self.rule.run, rmsg)
                vout = self._safe(fv, _TIMEOUT_VISION, "vision", task_id=task_id)
                rout = self._safe(fr, _TIMEOUT_RULE, "rule", task_id=task_id)
        except Exception as e:  # noqa: BLE001 顶层兜底
            self._push(task_id, "vision", "failed")
            self._push(task_id, "rule", "failed")
            return StageMessage(
                task_id=task_id, agent="orchestrator", status="failed",
                payload={}, error=f"{type(e).__name__}: {e}")

        self._push(task_id, "vision", vout.status, vout.cost_ms)
        self._push(task_id, "rule", rout.status, rout.cost_ms)

        # 视觉输出回灌规范段：预检阶段只查作业票，拿到视觉证据后再补 RAG，
        # 避免同一任务重复执行一次完整的规范检索。
        violation_descs = vout.payload.get("violation_descs", []) if vout.status == "success" else []
        permit_noncompliant = any(
            c.get("verdict") == "不合规"
            for c in rout.payload.get("compliance", [])
        ) if rout.status == "success" else False
        # quick 模式：跳过二阶段回灌，仅保留一阶段作业票预检结论（§5.10）
        if mode == "full" and rout.status == "success" and (violation_descs or permit_noncompliant):
            self._push(task_id, "rule", "running")
            rmsg = StageMessage(
                task_id=task_id, agent="rule", status="pending",
                payload={
                    "permit_info": permit_info,
                    "violation_descs": violation_descs,
                    "skip_rag": False,
                },
                error=None, cost_ms=0)
            # 二阶段 RAG 检索受超时预算保护；超时/异常则保留一阶段结论并降级
            rout2 = self._run_with_timeout(
                self.rule.run, rmsg,
                timeout=_TIMEOUT_RULE, agent="rule", task_id=task_id)
            if rout2.status == "success":
                rout = rout2
            else:
                rout = StageMessage(
                    task_id=task_id, agent="rule", status="degraded",
                    payload=rout.payload, error=rout2.error)
            self._push(task_id, "rule", rout.status, rout.cost_ms)

        # 融合
        self._push(task_id, "fusion", "running")
        fmsg = self._run_with_timeout(
            self.fusion.run,
            StageMessage(
                task_id=task_id, agent="fusion", status="pending",
                payload={
                    "detections": vout.payload.get("detections", []) if vout.status == "success" else [],
                    "compliance": rout.payload.get("compliance", []) if rout.status == "success" else [],
                }, error=None, cost_ms=0),
            timeout=_TIMEOUT_FUSION, agent="fusion", task_id=task_id)
        self._push(task_id, "fusion", fmsg.status, fmsg.cost_ms)

        # 复核：高风险或证据不足的结果标记人工复核
        self._push(task_id, "review", "running")
        rvmsg = self._run_with_timeout(
            self.review.run,
            StageMessage(
                task_id=task_id, agent="review", status="pending",
                payload={
                    "detections": vout.payload.get("detections", []) if vout.status == "success" else [],
                    "compliance": rout.payload.get("compliance", []) if rout.status == "success" else [],
                    "risk_level": fmsg.payload.get("risk_level", "一般"),
                    "filtered_fp": fmsg.payload.get("filtered_fp", []),
                }, error=None, cost_ms=0),
            timeout=_TIMEOUT_REVIEW, agent="review", task_id=task_id)
        self._push(task_id, "review", rvmsg.status, rvmsg.cost_ms)

        # 低置信度 LLM 辅助理解：needs_review 时后台发起（异步，不占
        # 主链路时延；结论落 agent_runs 证据链，仅辅助不改变定级）
        if rvmsg.status in ("success", "degraded") and                 rvmsg.payload.get("needs_review"):
            try:
                self.review.assist_async(
                    task_id,
                    vout.payload.get("detections", []) if vout.status == "success" else [],
                    rout.payload.get("compliance", []) if rout.status == "success" else [],
                    fmsg.payload.get("risk_level", "一般"),
                    rvmsg.payload.get("review_reasons", []))
            except Exception as exc:  # noqa: BLE001 辅助失败不影响主链路
                from core.logging import get_logger
                get_logger(__name__).warning(
                    f"任务 {task_id} LLM 辅助研判发起失败: {exc}")

        # 闭环处置
        self._push(task_id, "action", "running")
        amsg = self._run_with_timeout(
            self.action.run,
            StageMessage(
                task_id=task_id, agent="action", status="pending",
                payload={
                    "risk_level": fmsg.payload.get("risk_level", "一般"),
                    "reasons": fmsg.payload.get("reasons", []),
                    "compliance": rout.payload.get("compliance", []) if rout.status == "success" else [],
                    "training_tips": rout.payload.get("training_tips", []) if rout.status == "success" else [],
                    "needs_review": rvmsg.payload.get("needs_review", False),
                    "review_reasons": rvmsg.payload.get("review_reasons", []),
                }, error=None, cost_ms=0),
            timeout=_TIMEOUT_ACTION, agent="action", task_id=task_id)
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
        return StageMessage(
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
