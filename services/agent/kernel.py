"""Plan-and-Execute 受限范式内核（设计文档 §5.3，自研 ≤400 行）。

否决 ReAct 的落地：LLM 只在「规划」与「汇总」两点出场，中间执行完全
确定性——步数上限 + 墙钟总预算双闸，任一触顶强制收敛（降级汇总）。

关键约束（§5.3.2 / §5.6 / §5.7 / §7）：
- 计划解析失败重试 ≤1 次（计入预算），仍失败直接落失败态——
  截断不补全，绝不让 LLM 续写；
- 遇 side_effect 工具置 need_confirm 挂起，返 pending_confirm 等人工确认；
- 每步落 agent_chat_run_steps（失败也留痕）+ 心跳 updated_at；
- 步骤幂等：UNIQUE(run_id, step_idx) 已 success 的步骤直接跳过；
- digest 由代码拼接（≤300 字），零 LLM；
- 汇总保底预留 5s，不足则跳过汇总记 degraded。
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from pydantic import ValidationError

from core.logging import get_logger
from dao.models import AgentChatDAO
from services.agent.models import (LOCAL_PLAN_STEPS, MAX_PLAN_STEPS, Plan,
                                   RunContext, Step, StepResult)
from services.agent.playbooks import Playbook, get_playbook
from services.agent.tools import TOOL_REGISTRY, ToolCtx, ToolSpec, invoke_tool

log = get_logger(__name__)

# 汇总前保底预留（秒）：不足即跳过汇总、以 digest 降级作答（§7）
SYNTH_RESERVE_SEC = 5.0
# 单次规划调用的墙钟上限（计入 run 总预算）
PLAN_CALL_SEC = 20.0
# digest 硬上限（§5.7：≤300 字）
DIGEST_LIMIT = 300

# 交给 ChatClient 的轻量 JSON Schema（云端走 JSON mode；输出再过 Plan 校验）
PLAN_JSON_SCHEMA = {
    "type": "object",
    "required": ["goal", "steps"],
    "properties": {
        "goal": {"type": "string"},
        "steps": {"type": "array"},
    },
}


@dataclass
class RunOutcome:
    """内核执行结论（由 run_service 落终态）。"""

    status: str                              # completed/degraded/failed/pending_confirm/cancelled
    answer: str | None = None                # 最终回复文本（或 digest 降级作答）
    result_json: str | None = None           # 落 agent_chat_runs.result_json
    confirm_payload: dict | None = None      # 挂起时的副作用步骤确认卡
    error: str | None = None


def build_digest(ctx: RunContext) -> str:
    """代码生成会话摘要（模板拼接 + 字段截断，≤300 字，零 LLM，§5.7）。"""
    goal = (ctx.plan.goal if ctx.plan else "") or ctx.user_input
    parts = [f"目标:{goal[:80]}"]
    for s in ctx.steps:
        seg = f"{s.step_idx + 1}.{s.tool}({s.status})"
        detail = s.digest or s.error or ""
        if detail:
            seg += f" {detail}"
        parts.append(seg)
    text = "；".join(parts)
    return text[:DIGEST_LIMIT]


def validate_plan_obj(obj: dict, registry: dict[str, ToolSpec]) -> Plan:
    """计划二次校验：pydantic + 工具白名单。越界抛 ValidationError。

    供内核与 confirm（modified_plan）共用——改计划过同样的白名单与
    schema 校验（§5.6.2）。
    """
    plan = Plan.model_validate(obj)
    if not plan.steps:
        raise ValueError("计划至少包含一步")
    for st in plan.steps:
        if st.tool not in registry:
            raise ValueError(f"未知工具 {st.tool}（不在注册表白名单内）")
    # need_confirm 由代码按副作用强制，不信 LLM 自报（§5.8）
    plan.need_confirm = any(registry[st.tool].side_effect for st in plan.steps)
    return plan


class PlanExecutor:
    """规划 → 逐步执行 → 摘要回填 → 汇总。状态全部落库，内存无状态。"""

    def __init__(self, dao: AgentChatDAO, client,
                 registry: dict[str, ToolSpec] | None = None, *,
                 stop_check=None,
                 synthesize_reserve_sec: float = SYNTH_RESERVE_SEC) -> None:
        self._dao = dao
        self._client = client
        self._registry = registry if registry is not None else TOOL_REGISTRY
        # 取消探测：run_service 注入（查库 status==cancelled），每步前检查
        self._stop_check = stop_check
        self._reserve = float(synthesize_reserve_sec)
        self._start = time.monotonic()
        self._deadline = self._start + 30.0
        self._plan_degraded = False          # 规划命中本地档 → 整体降级标记
        # 已确认放行的副作用步骤索引（-1=无）：恢复时仅放行被确认的那一步，
        # 后续再遇副作用步骤仍须重新挂起确认（无豁免开关，§5.8）
        self._confirmed_step_idx = -1

    # ---------- 入口 ----------

    def run(self, ctx: RunContext) -> RunOutcome:
        """全新执行：规划（剧本预置计划优先）→ 执行循环。"""
        self._start = time.monotonic()
        self._deadline = self._start + float(ctx.deadline_sec)
        # 确定性剧本优先（v2.2）：已知意图由代码预置计划，规划零 LLM——
        # 思考型模型对结构化 JSON 输出不稳，已知流程不与随机性搏斗；
        # 预置计划异常时回退 LLM 规划。
        pb0 = get_playbook(ctx.intent)
        if pb0 is not None and pb0.plan_fn is not None:
            try:
                plan = validate_plan_obj(pb0.plan_fn(ctx), self._registry)
                ctx.plan = plan
                self._persist_plan(ctx)
                return self._execute_loop(ctx, start_idx=0)
            except Exception as exc:  # noqa: BLE001 预置失败回退 LLM 规划
                log.warning(f"剧本预置计划失败，回退 LLM 规划: {exc}")
        plan, err = self._plan(ctx)
        if plan is None:
            # LLM 全败且命中剧本 → 降级矩阵第 3 档：规则模板档作答（§6）
            pb = get_playbook(ctx.intent)
            if pb is not None:
                return self._template_outcome(ctx, pb, f"规划失败: {err}")
            if err == "budget_exhausted":
                return self._converge(ctx, "总预算耗尽（规划阶段），强制收敛")
            # 截断不补全：解析失败直接落失败态，不让 LLM 续写
            return RunOutcome("failed", error=f"规划失败: {err}")
        ctx.plan = plan
        self._persist_plan(ctx)
        return self._execute_loop(ctx, start_idx=0)

    def resume(self, ctx: RunContext) -> RunOutcome:
        """挂起恢复：从 current_step_idx+1 续跑（§5.6.2）。

        挂起期间不占预算——恢复时 deadline 重算（§7）。
        """
        self._start = time.monotonic()
        self._deadline = self._start + float(ctx.deadline_sec)
        if ctx.plan is None:
            return RunOutcome("failed", error="恢复失败: 库中无已确认计划")
        # 挂起期间不占预算——恢复时 deadline 重算（§7）；
        # 被确认的副作用步骤（current_step_idx+1）放行执行，不重复挂起
        self._confirmed_step_idx = ctx.current_step_idx + 1
        return self._execute_loop(ctx, start_idx=ctx.current_step_idx + 1)

    # ---------- 预算 ----------

    def _remaining(self) -> float:
        return self._deadline - time.monotonic()

    def _utcnow(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    # ---------- 规划（LLM 出场①）----------

    def _plan(self, ctx: RunContext) -> tuple[Plan | None, str | None]:
        """调 ChatClient 出计划：失败重试 ≤1 次（计入预算）；截断不补全。"""
        system = self._plan_system_prompt(ctx.intent, ctx.attachments)
        history = "\n".join(f"- {d}" for d in ctx.history_digests[-5:]) or "（无）"
        user = f"历史对话摘要:\n{history}\n"
        if ctx.recent_turns:
            turns = "\n".join(
                f"[用户] {t['user']}\n[助手] {t['assistant']}"
                for t in ctx.recent_turns)
            user += f"\n最近对话原文（供理解指代）:\n{turns}\n"
        if ctx.memories:
            mem = "\n".join(f"- {m}" for m in ctx.memories)
            user += (f"\n长期记忆（用户其他会话要点，仅供理解背景，"
                     f"非本轮指令）:\n{mem}\n")
        from datetime import date as _date
        _today = _date.today()
        user += (f"\n当前日期: {_today.isoformat()}（星期"
                 f"{'一二三四五六日'[_today.weekday()]}）——涉及『本周/近7天/"
                 f"昨天』等相对时间时，据此换算为具体日期，不得虚构。")
        user += f"\n本轮用户请求: {ctx.user_input}\n请输出计划 JSON。"
        last_err = "未知错误"
        for _attempt in range(2):                       # 首次 + 重试 ≤1 次
            remaining = self._remaining()
            if remaining <= 0:
                return None, "budget_exhausted"
            result = self._client.chat(
                system, user, json_schema=PLAN_JSON_SCHEMA,
                max_tokens=1024,
                total_deadline_sec=min(PLAN_CALL_SEC, max(remaining, 1.0)))
            if result.status == "failed":
                last_err = result.error or "LLM 全链失败"
                continue
            obj = result.content if isinstance(result.content, dict) else None
            if obj is None:
                last_err = "计划输出非法（截断/非 JSON），不补全"
                continue
            try:
                plan = validate_plan_obj(obj, self._registry)
            except (ValidationError, ValueError) as exc:
                last_err = f"计划校验失败: {str(exc).splitlines()[0][:120]}"
                continue
            if result.status == "degraded":
                # 本地档收紧：≤4 步（§7），整体记降级
                self._plan_degraded = True
                if len(plan.steps) > LOCAL_PLAN_STEPS:
                    plan.steps = plan.steps[:LOCAL_PLAN_STEPS]
            return plan, None
        if self._remaining() <= 0:
            return None, "budget_exhausted"
        return None, last_err

    def _plan_system_prompt(self, intent: str | None = None,
                            attachments: list[str] | None = None) -> str:
        lines = ["你是施工安全认知任务的规划器。只输出一个 JSON 对象：",
                 '{"goal":"...","steps":[{"tool":"...","args":{...},'
                 f'"reason":"≤60字"}}]}}，步骤数 ≤{MAX_PLAN_STEPS}。',
                 "tool 必须从以下白名单选取，args 必须符合该工具参数定义："]
        for name, spec in self._registry.items():
            try:
                props = list(spec.args_schema.model_json_schema().get(
                    "properties", {}).keys())
            except Exception:  # noqa: BLE001 schema 提取失败不阻断规划
                props = []
            mark = "（副作用，将人工确认）" if spec.side_effect else ""
            lines.append(f"- {name}({', '.join(props) or '无参'}): "
                         f"{spec.desc}{mark}")
        if attachments:
            vids = [a for a in attachments if a.lower().endswith(
                (".mp4", ".mov", ".avi", ".webm", ".mkv"))]
            imgs = [a for a in attachments if a.lower().endswith(
                (".jpg", ".jpeg", ".png", ".bmp", ".webp"))]
            lines.append("本次对话服务端已校验的附件（run_video_pipeline 的 "
                         "video/images 参数必须且只能取自以下路径，不得虚构）：")
            if vids:
                lines.append(f"- video 候选: {vids}")
            if imgs:
                lines.append(f"- images 候选: {imgs}")
            if not vids and not imgs:
                lines.append("- （无可识别的图像/视频附件，不要调用 "
                             "run_video_pipeline）")
        lines.append("原则：优先只读工具；不确定就少步骤；"
                     "不要输出 JSON 以外的任何字符。")
        pb = get_playbook(intent)
        if pb is not None and pb.system_prompt:
            lines.append(f"剧本提示（{pb.intent}）：{pb.system_prompt}")
        return "\n".join(lines)

    def _persist_plan(self, ctx: RunContext) -> None:
        self._dao.update_run(
            ctx.run_id,
            plan_json=ctx.plan.model_dump_json(),
            intent=ctx.intent,
            need_confirm=1 if ctx.plan.need_confirm else 0)

    # ---------- 执行循环 ----------

    def _execute_loop(self, ctx: RunContext, start_idx: int) -> RunOutcome:
        plan = ctx.plan
        idx = start_idx
        converge_reason = None
        while idx < len(plan.steps):
            if self._stop_check is not None and self._stop_check():
                return RunOutcome("cancelled", error="任务已被取消")
            # 双闸①步数上限 + ②墙钟总预算（§5.3.2）
            if idx >= MAX_PLAN_STEPS or self._remaining() <= 1.0:
                converge_reason = "总预算/步数耗尽，强制收敛"
                break
            step = plan.steps[idx]
            spec = self._registry.get(step.tool)
            if spec is None:                    # 白名单兜底（理论已在规划拦截）
                ctx.steps.append(self._record_failed(ctx, idx, step,
                                                     "未知工具（白名单外）"))
                idx += 1
                continue
            if spec.side_effect and idx != self._confirmed_step_idx:
                return self._suspend_for_confirm(ctx, idx, step)
            sr = self._execute_step(ctx, idx, step, spec)
            ctx.steps.append(sr)
            # 心跳：current_step_idx 与 updated_at 一并刷新（孤儿扫描依据）
            self._dao.update_run(ctx.run_id, current_step_idx=idx)
            idx += 1
        ctx.current_step_idx = idx - 1
        return self._finish(ctx, converge_reason)

    def _execute_step(self, ctx: RunContext, idx: int, step: Step,
                      spec: ToolSpec) -> StepResult:
        """执行单步：幂等恢复（已 success 跳过）+ schema 校验 + 超时裁剪。"""
        existing = self._dao.get_step(ctx.run_id, idx)
        if existing is not None and existing["status"] == "success":
            return StepResult(step_idx=idx, tool=step.tool, args=step.args,
                              status="success",
                              digest=existing["result_digest"],
                              cost_ms=existing["cost_ms"] or 0)
        # 参数二次校验（越界即拒，§5.13）
        try:
            args = spec.args_schema.model_validate(step.args or {}).model_dump()
        except ValidationError as exc:
            return self._record_failed(ctx, idx, step,
                                       f"参数校验失败: {str(exc).splitlines()[0][:120]}")
        args = self._bind_attachments(step.tool, args, ctx.attachments)
        if existing is None:
            try:
                self._dao.insert_step(
                    ctx.run_id, idx, step.tool,
                    json.dumps(args, ensure_ascii=False), status="pending")
            except sqlite3.IntegrityError:
                # UNIQUE 冲突：并发/恢复场景，重查已 success 则跳过
                again = self._dao.get_step(ctx.run_id, idx)
                if again is not None and again["status"] == "success":
                    return StepResult(step_idx=idx, tool=step.tool,
                                      args=args, status="success",
                                      digest=again["result_digest"],
                                      cost_ms=again["cost_ms"] or 0)
        # 剩余时间裁剪工具 timeout（§7 预算口径）
        budget = min(spec.timeout_sec, max(self._remaining(), 0.1))
        t0 = time.monotonic()
        out = invoke_tool(step.tool, spec, args,
                          ToolCtx(user_id=ctx.user_id, role=ctx.role,
                                  run_id=ctx.run_id),
                          timeout_sec=budget)
        cost_ms = int((time.monotonic() - t0) * 1000)
        status = out.get("status", "failed")
        data = out.get("data")
        digest = (json.dumps(data, ensure_ascii=False)[:200]
                  if data is not None else None)
        # 改计划替换执行：挂起时落的 pending 行工具名与实际执行不一致时
        # 回填 tool/args，证据链与真实执行一致（§5.6.2）
        replaced = (existing is not None
                    and existing["tool"] != step.tool)
        self._dao.update_step(
            ctx.run_id, idx, status, result_digest=digest,
            error=out.get("error"), cost_ms=cost_ms,
            tool=step.tool if replaced else None,
            args_json=(json.dumps(args, ensure_ascii=False)
                       if replaced else None))
        return StepResult(step_idx=idx, tool=step.tool, args=args,
                          status=status, digest=digest,
                          error=out.get("error"), cost_ms=cost_ms)

    @staticmethod
    def _bind_attachments(tool: str, args: dict,
                          attachments: list[str]) -> dict:
        """服务端强制绑定附件路径（§5.13 作用域不经 LLM 的附件版）。

        LLM 规划的 run_video_pipeline 参数仅作意图参考：video/images 一律
        以服务端校验过的附件清单为准（视频取首个视频类附件、图像取全部
        图像类附件），杜绝 LLM 虚构路径读取任意文件。
        """
        if tool != "run_video_pipeline" or not attachments:
            return args
        vids = [a for a in attachments if a.lower().endswith(
            (".mp4", ".mov", ".avi", ".webm", ".mkv"))]
        imgs = [a for a in attachments if a.lower().endswith(
            (".jpg", ".jpeg", ".png", ".bmp", ".webp"))]
        if not vids and not imgs:
            return args
        out = dict(args)
        out["video"] = vids[0] if vids else None
        out["images"] = imgs
        return out

    def _record_failed(self, ctx: RunContext, idx: int, step: Step,
                       error: str) -> StepResult:
        """校验类失败也落步骤行留痕（失败不静默）。"""
        if self._dao.get_step(ctx.run_id, idx) is None:
            try:
                self._dao.insert_step(
                    ctx.run_id, idx, step.tool,
                    json.dumps(step.args or {}, ensure_ascii=False),
                    status="pending")
            except sqlite3.IntegrityError:
                pass
        self._dao.update_step(ctx.run_id, idx, "failed", error=error)
        return StepResult(step_idx=idx, tool=step.tool, args=step.args,
                          status="failed", error=error)

    # ---------- 挂起（副作用人工确认，§5.6）----------

    def _suspend_for_confirm(self, ctx: RunContext, idx: int,
                             step: Step) -> RunOutcome:
        payload = {"step_idx": idx, "tool": step.tool, "args": step.args,
                   "reason": step.reason, "suspended_at": self._utcnow()}
        # 挂起步骤先落 pending 行（证据链可见待确认项）
        if self._dao.get_step(ctx.run_id, idx) is None:
            try:
                self._dao.insert_step(
                    ctx.run_id, idx, step.tool,
                    json.dumps(step.args or {}, ensure_ascii=False),
                    status="pending")
            except sqlite3.IntegrityError:
                pass
        self._dao.update_run(
            ctx.run_id,
            plan_json=ctx.plan.model_dump_json(),
            need_confirm=1,
            confirm_payload=json.dumps(payload, ensure_ascii=False))
        ok = self._dao.transition_status(ctx.run_id, "running",
                                         "pending_confirm")
        if not ok:                                   # 已被取消等竞争场景
            return RunOutcome("cancelled", error="挂起竞争失败（状态已变更）")
        ctx.status = "pending_confirm"
        return RunOutcome("pending_confirm", confirm_payload=payload)

    # ---------- 收敛与汇总 ----------

    def _converge(self, ctx: RunContext, reason: str) -> RunOutcome:
        """预算耗尽：以已有结果降级收敛（无步骤时仅留 digest）。"""
        digest = build_digest(ctx)
        result_json = json.dumps(
            {"answer": digest, "digest": digest, "degraded_reason": reason},
            ensure_ascii=False)
        return RunOutcome("degraded", answer=digest, result_json=result_json,
                          error=reason)

    def _finish(self, ctx: RunContext, converge_reason: str | None) -> RunOutcome:
        """汇总（LLM 出场②）：保底预留 5s，不足跳过汇总记 degraded。"""
        digest = build_digest(ctx)
        steps_degraded = any(s.status != "success" for s in ctx.steps)
        base = ("degraded"
                if (converge_reason or steps_degraded or self._plan_degraded)
                else "completed")
        # 剧本专属汇总（§5.9）：键路径回溯校验 + 重写一次 + 模板档降级
        pb = get_playbook(ctx.intent)
        if (pb is not None and pb.validate_fn is not None
                and converge_reason is None
                and self._remaining() >= self._reserve):
            return self._finish_playbook_synth(ctx, pb, digest, base)
        answer: str | None = None
        synth_error: str | None = None
        remaining = self._remaining()
        if converge_reason is None and remaining >= self._reserve:
            result = self._client.chat(
                self._synth_system_prompt(),
                f"执行摘要:\n{digest}\n\n用户请求: {ctx.user_input}",
                max_tokens=800,
                total_deadline_sec=min(remaining - 1.0, PLAN_CALL_SEC))
            if result.status != "failed" and result.content:
                answer = str(result.content)
            else:
                base = "degraded"
                synth_error = result.error or "汇总调用失败"
        else:
            if base == "completed":
                base = "degraded"       # 预算不足跳过汇总（§7）
            synth_error = converge_reason or "剩余预算不足，跳过汇总"
        if answer is None:
            answer = digest
        result_json = json.dumps(
            {"answer": answer, "digest": digest,
             "degraded_reason": converge_reason or synth_error},
            ensure_ascii=False)
        return RunOutcome(base, answer=answer, result_json=result_json,
                          error=(converge_reason or synth_error))

    def _synth_system_prompt(self) -> str:
        return ("你是施工安全助手。根据执行摘要用中文写出简洁最终回复，"
                "数字与结论必须忠实于摘要，不得编造；"
                "摘要标记为 degraded/failed 的步骤要如实说明局限。")

    # ---------- 剧本汇总（§5.9：键路径回溯校验 + 降级重写）----------

    def _template_outcome(self, ctx: RunContext, pb: Playbook,
                          reason: str) -> RunOutcome:
        """规则模板档作答（降级矩阵第 3 档）：零 LLM，记 degraded 留痕。"""
        digest = build_digest(ctx)
        try:
            answer = pb.template_fn(ctx.user_input)
        except Exception as exc:  # noqa: BLE001 模板档自身异常也要可读收敛
            answer = f"模板档生成异常: {type(exc).__name__}: {exc}"
        result_json = json.dumps(
            {"answer": answer, "digest": digest, "degraded_reason": reason},
            ensure_ascii=False)
        return RunOutcome("degraded", answer=answer, result_json=result_json,
                          error=reason)

    def _finish_playbook_synth(self, ctx: RunContext, pb: Playbook,
                               digest: str, base: str) -> RunOutcome:
        """剧本汇总：以确定性统计为事实源生成叙述并做键路径回溯校验。

        校验未达标 → 携错误明细自动降级重写一次（重新汇总调用）；
        二次仍未达标 → 落规则模板档并记 degraded（§5.9/§6）。
        """
        stats: dict | None = None
        if pb.stats_fn is not None:
            try:
                stats = pb.stats_fn(ctx.steps)
            except Exception as exc:  # noqa: BLE001 事实源不可得 → 直接模板档
                log.warning(f"剧本 {pb.intent} 统计事实源获取失败: "
                            f"{type(exc).__name__}: {exc}")
        if not stats:
            return self._template_outcome(ctx, pb, "统计事实源获取失败，落模板档")
        payload = json.dumps(stats, ensure_ascii=False)
        errors: list[str] = []
        for _attempt in range(2):                      # 首次 + 降级重写 ≤1 次
            user = (f"统计数据 JSON（gather() 返回）:\n{payload}\n\n"
                    f"用户请求: {ctx.user_input}\n\n请撰写周报正文。")
            if errors:
                user += ("\n\n上一稿未通过键路径回溯校验，问题如下:\n- "
                         + "\n- ".join(errors)
                         + "\n请严格按约束重写，每个数字必须标注正确键路径。")
            result = self._client.chat(
                pb.synth_prompt or self._synth_system_prompt(), user,
                max_tokens=800,
                total_deadline_sec=min(max(self._remaining() - 1.0, 1.0),
                                       PLAN_CALL_SEC))
            if result.status != "failed" and result.content:
                text = str(result.content)
                ok, errors = pb.validate_fn(text, stats)
                if ok:
                    result_json = json.dumps(
                        {"answer": text, "digest": digest,
                         "degraded_reason": None, "traceback_verified": True},
                        ensure_ascii=False)
                    return RunOutcome(base, answer=text,
                                      result_json=result_json)
            else:
                errors = [result.error or "汇总调用失败"]
        # 二次仍未达标 → 模板档兜底（记 degraded）
        return self._template_outcome(
            ctx, pb, "汇总两次未通过键路径回溯校验，落模板档")
