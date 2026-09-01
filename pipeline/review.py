"""复核段：对高风险或证据不足的结果标记人工复核。

它不是直接改判，而是把“需要人工确认”的结论显式交给安全员，
与人工纠偏闭环共同构成 AI 纠偏管理能力。
辅助研判的 LLM 调用经统一入口 ChatClient（v2.1 §5.1，云端→本地降级）。
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor

from pipeline.base import StageBase, StageMessage
from core.chat_client import get_chat_client
from core.logging import get_logger

# 高风险类别在置信度低于该值时进入复核
REVIEW_CONF_THRESHOLD = 0.55

# 低置信度 LLM 辅助研判：单 worker 串行化（LLM 并发量低）；
# 仅辅助理解（原因分析/复核要点/优先级建议），不改变规则定级
_ASSIST_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="llm-assist")
log = get_logger(__name__)
HIGH_RISK_CLASSES = {
    "spark", "smoke", "no_helmet", "no_vest",
    "face_shield", "extinguisher",
}
HIGH_RISK_LEVELS = {"较大", "重大"}


class ReviewStage(StageBase):
    """复核段：消费融合结果，输出是否需要人工复核。"""

    def _execute(self, msg: StageMessage) -> StageMessage:
        detections = msg.payload.get("detections", []) or []
        compliance = msg.payload.get("compliance", []) or []
        risk_level = msg.payload.get("risk_level", "低")
        reasons: list[str] = []

        # 高风险类别低置信度：无论风险等级都须复核
        # （原逻辑仅在 risk_level 为较大/重大时才查，导致 PPE 类
        #   不在 hot_work 矩阵、风险升不上去时复核被完全跳过）
        for det in detections:
            cls = det.get("cls")
            conf = float(det.get("conf", 1.0) or 1.0)
            if cls in HIGH_RISK_CLASSES and conf < REVIEW_CONF_THRESHOLD:
                reasons.append(
                    f"{cls} 置信度 {conf:.2f} 低于 {REVIEW_CONF_THRESHOLD:.2f}，"
                    "属高风险项，建议人工复核")
        for item in compliance:
            if item.get("needs_review"):
                reasons.append(
                    f"规范条款未明确匹配：{item.get('label', '')}，建议人工补充依据")

        # 作业票不合规但影像未检出可直接定级的违规目标：
        # 证据不足，需人工复核（闭环 fusion「请人工复核」提示，SRS 3.2.3）
        permit_noncompliant = any(c.get("verdict") == "不合规" for c in compliance)
        if permit_noncompliant and risk_level in ("一般", "低"):
            reasons.append("作业票不合规但影像未检出违规目标，证据不足，建议人工复核")

        msg.status = "success"
        msg.payload = {
            "needs_review": bool(reasons),
            "review_reasons": reasons,
            "input_summary": {
                "risk_level": risk_level,
                "detections": len(detections),
                "compliance": len(compliance),
            },
        }
        return msg

    # ---------- 低置信度 LLM 辅助理解（异步，不进主链路时延） ----------

    def assist_async(self, task_id: str, detections: list[dict],
                     compliance: list[dict], risk_level: str,
                     reasons: list[str],
                     db_path: str | None = None) -> None:
        """needs_review 时后台调 LLM 产出辅助研判意见，落证据链(agent_runs)。

        设计边界：①结论仅为"辅助理解"文本（低置信度可能原因/现场复核要点/
        建议优先级），不改变规则定级；②异步执行，不占主链路时延预算；
        ③LLM 不可用/异常时静默留痕降级，与 polish/ask_json 口径一致；
        ④db_path 与调用方同库（文件库/patched 库均可；:memory: 库跨连接
        不可见，该环境降级为仅日志留痕）。
        """
        if not task_id or not reasons:
            return
        _ASSIST_POOL.submit(self._assist_worker, task_id, detections,
                            compliance, risk_level, reasons, db_path)

    def _assist_worker(self, task_id: str, detections: list[dict],
                       compliance: list[dict], risk_level: str,
                       reasons: list[str],
                       db_path: str | None = None) -> None:
        from services.db import scoped
        from dao.models import AgentRunDAO

        def _row(status: str, output: dict, error: str | None,
                 cost_ms: int) -> None:
            payload_in = json.dumps({
                "risk_level": risk_level,
                "detections": [
                    {"cls": d.get("cls"), "conf": d.get("conf")}
                    for d in detections[:10]],
                "review_reasons": reasons,
            }, ensure_ascii=False)
            # 独立连接重试 3 次(间隔 0.5s):主流程任务行提交与辅助落库
            # 存在提交时序竞争;:memory: 库跨连接不可见,该环境降级为仅日志
            last_err: Exception | None = None
            for attempt in range(3):
                try:
                    with scoped(db_path) as conn:
                        AgentRunDAO(conn).bulk_insert([{
                            "task_id": task_id, "agent": "llm_assist",
                            "status": status, "cost_ms": cost_ms,
                            "input_json": payload_in,
                            "output_json": json.dumps(output, ensure_ascii=False),
                            "error": error,
                        }], commit=True)
                    return
                except Exception as exc:  # noqa: BLE001
                    last_err = exc
                    if attempt < 2:
                        time.sleep(0.5)
            if last_err is not None:
                log.warning(
                    f"任务 {task_id} LLM 辅助研判落库失败(仅日志留痕): {last_err}")

        t0 = time.monotonic()
        try:
            client = get_chat_client()
            if not client.available_provider():
                _row("skipped",
                     {"advice": None, "review_reasons": reasons},
                     "LLM 不可用", 0)
                return
            system = (
                "你是施工现场安全研判的辅助分析员。规则引擎已给出定级，"
                "你只负责帮助人工复核者理解低置信度检测，严禁改变或质疑定级结论，"
                "严禁编造检测到的事实。输出不超过 200 字，用三行："
                "可能原因（遮挡/距离/光线/相似类别混淆等）、"
                "现场复核要点（按类别给具体检查项）、建议处置优先级。")
            user = json.dumps({
                "风险等级": risk_level,
                "检测项": [{"类别": d.get("cls"),
                            "置信度": round(float(d.get("conf", 0) or 0), 2),
                            "场景": d.get("scene")} for d in detections[:10]],
                "进入复核的原因": reasons,
            }, ensure_ascii=False)
            result = client.chat(system, user, max_tokens=220,
                                 total_deadline_sec=30.0)
            cost = int((time.monotonic() - t0) * 1000)
            advice = (result.content.strip()
                      if isinstance(result.content, str) else None)
            if advice:
                _row("success",
                     {"advice": advice,
                      "review_reasons": reasons},
                     None, cost)
                log.info(f"任务 {task_id} LLM 辅助研判完成"
                         f"（{result.provider}/{cost}ms）")
            else:
                _row("failed", {"advice": None, "review_reasons": reasons},
                     result.error or "LLM 空输出", cost)
        except Exception as exc:  # noqa: BLE001 辅助失败静默降级留痕
            log.warning(f"任务 {task_id} LLM 辅助研判失败: {exc}")
            try:
                _row("failed", {"advice": None, "review_reasons": reasons},
                     f"{type(exc).__name__}: {exc}", 0)
            except Exception:  # noqa: BLE001
                pass
