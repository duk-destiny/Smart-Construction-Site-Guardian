"""PlanRunService：认知任务生命周期（设计文档 §5.6，M2）。

并发模式仿 `services/task_service.py`（Phase 4 已验证）：
- 类级锁 + 模块内线程池 + 原子「查再置」防 TOCTOU（对应其 L38-39、L100-106）；
- worker 内 `from services.db import scoped` 自开自关连接，与请求生命周期
  解耦（对应其 L117-118 的跨线程悬空句柄修复）。

本模块新增符号（非既有代码符号）：
- `_RUN_LOCK`：认知层自己的新锁——所有状态翻转「查再置」的原子性保障，
  与条件 UPDATE（AgentChatDAO.transition_status）构成双保险；
- `_EXECUTOR`：认知层独立线程池（与上传链路线程池物理隔离）。

状态机：pending → running → pending_confirm → running → completed/degraded/failed；
任一非终态可置 cancelled。孤儿扫描（§5.6.3）：
- pending/running 且 updated_at 超 60s → failed（进程重启，不自动重跑）；
- pending_confirm 保持可恢复，24h 未确认由读路径惰性置 cancelled。
"""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from core.logging import get_logger
from dao.models import AgentChatDAO, AuditDAO
from pydantic import ValidationError
from services.agent.kernel import PlanExecutor, RunOutcome, validate_plan_obj
from services.agent.models import RunContext, StepResult
from services.agent.tools import TOOL_REGISTRY

log = get_logger(__name__)


class PlanRunBusy(RuntimeError):
    """活跃 run 超限（准入背压）：端点据此同步返 {"status":"busy"}。"""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _agent_cfg() -> dict:
    """认知层上下文/记忆配置（agent.*，缺省全开）。"""
    try:
        from core.config import shared_config
        cfg = shared_config().get("agent") or {}
        return cfg if isinstance(cfg, dict) else {}
    except Exception:  # noqa: BLE001 配置缺失走默认
        return {}


def _load_recent_turns(dao: AgentChatDAO, row: dict,
                       limit: int = 2) -> list[dict]:
    """本会话最近 N 轮原文（user/assistant 配对，双方截断 ≤200 字）。

    供规划理解指代（"刚才那张单"），与 digest 摘要互补；只取已完结轮次
    （排除当前 run 的消息），零 LLM、纯查询。
    """
    turns: list[dict] = []
    cur: dict | None = None
    msgs = dao.list_messages(row["session_id"], limit=60)
    for m in msgs:
        if m["run_id"] == row["id"]:
            continue          # 当前 run 的消息不计入历史轮
        if m["role"] == "user":
            if cur:
                turns.append(cur)
            cur = {"user": (m["content"] or "")[:200]}
        elif (m["role"] == "assistant" and cur is not None
              and "assistant" not in cur):
            cur["assistant"] = (m["content"] or "")[:200]
            turns.append(cur)
            cur = None
    if cur:
        turns.append(cur)
    cfg = _agent_cfg()
    n = int(cfg.get("recent_turns", 2) or 0)
    return turns[-n:] if n > 0 else []


def _load_memories(dao: AgentChatDAO, row: dict,
                   limit: int = 2) -> list[str]:
    """跨会话记忆：用户最近其他会话的要点（digest ≤120 字/条）。

    记忆仅作背景理解注入规划（标注"非本轮指令"），不参与工具参数；
    agent.memory_enabled=false 时整体关闭。
    """
    cfg = _agent_cfg()
    if not cfg.get("memory_enabled", True):
        return []
    k = int(cfg.get("memory_sessions", limit) or 0)
    if k <= 0:
        return []
    sess_rows = dao.conn.execute(
        "SELECT id, title FROM chat_sessions WHERE user_id=? AND id!=? "
        "ORDER BY COALESCE(updated_at, created_at) DESC LIMIT ?",
        (row["user_id"], row["session_id"], k)).fetchall()
    out: list[str] = []
    for sess in sess_rows:
        mem = dao.conn.execute(
            "SELECT digest FROM chat_messages WHERE session_id=? "
            "AND digest IS NOT NULL AND digest!='' "
            "ORDER BY created_at DESC, id DESC LIMIT 1", (sess["id"],)).fetchone()
        if mem and mem["digest"]:
            title = (sess["title"] or "历史会话")[:20]
            out.append(f"（{title}）{mem['digest'][:120]}")
    return out


def _load_attachments(row: dict) -> list[str]:
    """读 run 行的附件 JSON（损坏/缺失一律回空表，不阻断执行）。"""
    try:
        data = json.loads(row.get("attachments_json") or "[]")
        return [str(x) for x in data] if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


class PlanRunService:
    """认知任务调度服务（内存无状态，状态全落库；进程重启可恢复）。"""

    # 认知层自己的新锁（本改造新增符号，非 TaskService._STATE_LOCK）：
    # 「查再置」原子性保障，杜绝 TOCTOU 双线程恢复（§5.6.2/§5.6.3）
    _RUN_LOCK = threading.Lock()
    # 认知层独立线程池（与上传研判的 TaskService._EXECUTOR 隔离）
    _EXECUTOR = ThreadPoolExecutor(max_workers=4)
    # 活跃 run 登记（准入背压）：run_id -> True；worker 结束即摘除
    _active_runs: dict[str, bool] = {}
    _MAX_ACTIVE = 4
    _ORPHAN_STALE_SEC = 60            # 孤儿判定阈值（§5.6.3）
    _CONFIRM_TTL_SEC = 24 * 3600      # pending_confirm 确认超时（§7）

    # 测试注入口（仿 TaskService._ORCH_FACTORY 范式）：
    # _CHAT_FACTORY: 可替换的 ChatClient 工厂（None → get_chat_client()）；
    # _REGISTRY:     可替换的工具注册表（None → TOOL_REGISTRY）
    _CHAT_FACTORY = None
    _REGISTRY = None

    # ---------- 内部工具 ----------

    @classmethod
    def _client(cls):
        if cls._CHAT_FACTORY is not None:
            return cls._CHAT_FACTORY()
        from core.chat_client import get_chat_client
        return get_chat_client()

    @classmethod
    def _registry(cls):
        return cls._REGISTRY if cls._REGISTRY is not None else TOOL_REGISTRY

    @staticmethod
    def _lazy_expire_confirm(dao: AgentChatDAO, row) -> dict:
        """惰性 24h 取消：读路径发现超期 pending_confirm 即置 cancelled（§7）。"""
        if row["status"] != "pending_confirm":
            return dict(row)
        try:
            updated = datetime.strptime(row["updated_at"], "%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError):
            return dict(row)
        if _utcnow() - updated > timedelta(seconds=PlanRunService._CONFIRM_TTL_SEC):
            if dao.transition_status(row["id"], "pending_confirm", "cancelled",
                                     error="超过 24h 未确认，自动取消"):
                log.info(f"认知任务 {row['id']} 确认超时，惰性取消")
            return dict(dao.get_run(row["id"]))
        return dict(row)

    # ---------- 创建 ----------

    @classmethod
    def create_run(cls, user_id: str, session_id: str, text: str,
                   intent: str | None = None,
                   deadline_sec: float | None = None,
                   attachments: list[str] | None = None) -> str:
        """建 run 即 submit（worker 内自开连接）；活跃超限抛 PlanRunBusy。

        意图接线（§5.9/§5.11 规则层）：调用方未显式指定 intent 时，
        由剧本规则检测（零 LLM）识别封闭意图并路由到对应剧本；
        未显式指定预算时采用剧本级墙钟预算。
        """
        from services.agent.playbooks import detect_intent, get_playbook
        intent = intent or detect_intent(text)
        if deadline_sec is None:
            pb = get_playbook(intent)
            deadline_sec = pb.deadline_sec if pb is not None else 30.0
        from services.db import scoped
        with scoped() as conn:
            dao = AgentChatDAO(conn)
            sess = dao.get_session(session_id)
            if sess is None or sess["user_id"] != user_id:
                raise ValueError("会话不存在或不属于当前用户")
            with cls._RUN_LOCK:                     # 原子「查再置」：准入背压
                if len(cls._active_runs) >= cls._MAX_ACTIVE:
                    raise PlanRunBusy("认知任务通道繁忙，请稍后再试")
                run_id = dao.create_run(
                    session_id, user_id, text, intent=intent,
                    deadline_sec=deadline_sec,
                    attachments_json=(json.dumps(attachments,
                                                 ensure_ascii=False)
                                      if attachments else None))
                cls._active_runs[run_id] = True
            AuditDAO(conn).insert(
                user_id, "agent_chat_create",
                json.dumps({"run_id": run_id, "session_id": session_id,
                            "intent": intent}, ensure_ascii=False))
        cls._EXECUTOR.submit(cls._worker, run_id, False)
        return run_id

    # ---------- 后台 worker ----------

    @classmethod
    def _worker(cls, run_id: str, resume: bool) -> None:
        try:
            # worker 与请求生命周期解耦：自开自关连接（仿 task_service 修复）
            from services.db import scoped
            with scoped() as conn:
                dao = AgentChatDAO(conn)
                row = dao.get_run(run_id)
                if row is None:
                    return
                if not resume:
                    # 条件翻转 pending→running；失败=已被取消/孤儿处置
                    if not dao.transition_status(run_id, "pending", "running"):
                        return
                ctx = cls._build_ctx(dao, dict(row))
                if ctx is None or (ctx.plan is None and resume):
                    dao.transition_status(run_id, "running", "failed",
                                          error="恢复失败: 库中无计划")
                    return
                executor = PlanExecutor(
                    dao, cls._client(), cls._registry(),
                    stop_check=lambda: (
                        (dao.get_run(run_id) or {"status": "cancelled"})["status"]
                        == "cancelled"))
                outcome = (executor.resume(ctx) if resume
                           else executor.run(ctx))
                cls._land_outcome(dao, dict(row), ctx, outcome)
        except Exception as exc:  # noqa: BLE001 worker 崩溃也要落可读终态
            log.warning(f"认知任务 {run_id} 执行异常: "
                        f"{type(exc).__name__}: {exc}")
            try:
                from services.db import scoped
                with scoped() as conn:
                    dao = AgentChatDAO(conn)
                    row = dao.get_run(run_id)
                    if row is not None and row["status"] in ("pending", "running"):
                        dao.transition_status(
                            run_id, row["status"], "failed",
                            error=f"{type(exc).__name__}: {exc}")
            except Exception:  # noqa: BLE001 兜底失败仅留日志
                log.exception(f"认知任务 {run_id} 终态落库失败")
        finally:
            with cls._RUN_LOCK:
                cls._active_runs.pop(run_id, None)

    @classmethod
    def _build_ctx(cls, dao: AgentChatDAO, row: dict) -> RunContext | None:
        """由库行重建 RunContext（含历史摘要与已完成步骤，供幂等恢复）。"""
        from services.agent.models import Plan
        plan = None
        if row.get("plan_json"):
            try:
                plan = Plan.model_validate_json(row["plan_json"])
            except ValidationError:
                plan = None
        history = [m["digest"] for m in
                   dao.list_messages(row["session_id"], limit=20)
                   if m["digest"] and m["run_id"] != row["id"]]
        steps: list[StepResult] = []
        for s in dao.list_steps(row["id"]):
            if s["status"] in ("success", "degraded", "failed"):
                try:
                    args = json.loads(s["args_json"] or "{}")
                except json.JSONDecodeError:
                    args = {}
                steps.append(StepResult(
                    step_idx=s["step_idx"], tool=s["tool"], args=args,
                    status=s["status"], digest=s["result_digest"],
                    error=s["error"], cost_ms=s["cost_ms"] or 0))
        user_row = dao.conn.execute(
            "SELECT role FROM users WHERE id=?", (row["user_id"],)).fetchone()
        return RunContext(
            run_id=row["id"], session_id=row["session_id"],
            user_id=row["user_id"],
            role=user_row["role"] if user_row else "",
            intent=row.get("intent"), user_input=row["user_input"],
            plan=plan, status=row["status"],
            current_step_idx=row["current_step_idx"],
            deadline_sec=float(row["deadline_sec"] or 30.0),
            steps=steps, history_digests=history,
            attachments=_load_attachments(row),
            recent_turns=_load_recent_turns(dao, row),
            memories=_load_memories(dao, row))

    @classmethod
    def _land_outcome(cls, dao: AgentChatDAO, row: dict, ctx: RunContext,
                      outcome: RunOutcome) -> None:
        """终态写回 + 助手消息（只存摘要，§5.7）+ 审计。"""
        if outcome.status in ("pending_confirm", "cancelled"):
            return                       # 挂起已在内核落库；取消由 cancel 落
        # 终态/助手消息/审计单次提交：读端（历史页在 run 完结即刷新）不会
        # 撞见「状态已完结但助手回复尚未落库」的中间态（CI 慢机上实测暴露）
        ok = dao.transition_status(row["id"], "running", outcome.status,
                                   error=outcome.error,
                                   result_json=outcome.result_json,
                                   commit=False)
        if not ok:
            dao.conn.rollback()          # 竞态下已被取消/孤儿处置，不覆盖
            return
        digest = None
        if outcome.result_json:
            try:
                digest = (json.loads(outcome.result_json).get("digest")
                          or "")[:300]
            except json.JSONDecodeError:
                digest = None
        dao.insert_message(row["session_id"], "assistant",
                           outcome.answer or "", run_id=row["id"],
                           intent=row.get("intent"), digest=digest,
                           commit=False)
        AuditDAO(dao.conn).insert(
            row["user_id"], "agent_chat_finish",
            json.dumps({"run_id": row["id"], "status": outcome.status},
                       ensure_ascii=False), commit=False)
        dao.conn.commit()

    # ---------- 确认 / 取消 ----------

    @classmethod
    def confirm(cls, run_id: str, user_id: str, action: str,
                modified_plan: dict | None = None) -> dict | None:
        """确认/取消挂起任务（§5.6.2）。

        原子「查再置」：仅 pending_confirm→running 成功者提交续跑，
        重复 confirm 第二次条件 UPDATE 必失败 → 只返回当前状态（幂等）。
        非属主返回 None（端点转 404，不泄露存在性）。
        """
        from services.db import scoped
        with scoped() as conn:
            dao = AgentChatDAO(conn)
            row = dao.get_run(run_id)
            if row is None or row["user_id"] != user_id:
                return None
            row = cls._lazy_expire_confirm(dao, dict(row))
            if action == "cancel":
                ok = dao.transition_status(run_id, "pending_confirm",
                                           "cancelled", error="用户取消")
                if ok:
                    AuditDAO(conn).insert(user_id, "agent_chat_cancel",
                                          json.dumps({"run_id": run_id},
                                                     ensure_ascii=False))
                return {"status": "cancelled" if ok
                        else dao.get_run(run_id)["status"]}
            if action != "confirm":
                raise ValueError("action 仅支持 confirm / cancel")
            if row["status"] != "pending_confirm":
                return {"status": row["status"]}      # 幂等：非挂起态直返
            plan_json = row["plan_json"]
            if modified_plan:
                # 改计划过同样的白名单 + schema 校验（§5.6.2）
                try:
                    plan = validate_plan_obj(modified_plan, cls._registry())
                except (ValidationError, ValueError) as exc:
                    raise ValueError(f"修改后的计划无效: {exc}") from exc
                plan_json = plan.model_dump_json()
            with cls._RUN_LOCK:                        # 查再置，防双线程恢复
                if not dao.transition_status(run_id, "pending_confirm",
                                             "running"):
                    return {"status": dao.get_run(run_id)["status"]}
                if modified_plan:
                    dao.update_run(run_id, plan_json=plan_json)
                AuditDAO(conn).insert(user_id, "agent_chat_confirm",
                                      json.dumps({"run_id": run_id},
                                                 ensure_ascii=False))
            cls._EXECUTOR.submit(cls._worker, run_id, True)
            return {"status": "running"}

    @classmethod
    def cancel(cls, run_id: str, user_id: str) -> dict | None:
        """执行中/挂起中取消（条件翻转；未执行副作用一律不执行）。"""
        from services.db import scoped
        with scoped() as conn:
            dao = AgentChatDAO(conn)
            row = dao.get_run(run_id)
            if row is None or row["user_id"] != user_id:
                return None
            for expected in ("pending", "running", "pending_confirm"):
                if dao.transition_status(run_id, expected, "cancelled",
                                         error="用户取消"):
                    AuditDAO(conn).insert(user_id, "agent_chat_cancel",
                                          json.dumps({"run_id": run_id},
                                                     ensure_ascii=False))
                    return {"ok": True, "status": "cancelled"}
            return {"ok": False, "status": row["status"]}

    # ---------- 读路径 ----------

    @classmethod
    def progress(cls, run_id: str, user_id: str) -> dict | None:
        """进度轮询：状态 + 计划 + 步骤 + 确认卡（含惰性 24h 取消）。"""
        from services.db import scoped
        with scoped() as conn:
            dao = AgentChatDAO(conn)
            row = dao.get_run(run_id)
            if row is None or row["user_id"] != user_id:
                return None
            row = cls._lazy_expire_confirm(dao, dict(row))
            return cls._run_view(dao, row, with_steps=True)

    @classmethod
    def trace(cls, run_id: str, user_id: str) -> dict | None:
        """完整证据链：计划 + 每步摘要 + 降级原因（§5.12）。"""
        return cls.progress(run_id, user_id)

    @classmethod
    def history(cls, session_id: str, user_id: str,
                limit: int = 200) -> list | None:
        """会话历史（只存摘要，§5.7）；非属主返回 None。"""
        from services.db import scoped
        with scoped() as conn:
            dao = AgentChatDAO(conn)
            sess = dao.get_session(session_id)
            if sess is None or sess["user_id"] != user_id:
                return None
            return [dict(m) for m in dao.list_messages(session_id, limit)]

    @staticmethod
    def _run_view(dao: AgentChatDAO, row: dict, with_steps: bool) -> dict:
        def _loads(text):
            if not text:
                return None
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return None

        view = {
            "run_id": row["id"], "session_id": row["session_id"],
            "status": row["status"], "intent": row.get("intent"),
            "current_step_idx": row["current_step_idx"],
            "need_confirm": bool(row["need_confirm"]),
            "plan": _loads(row["plan_json"]),
            "confirm_payload": _loads(row["confirm_payload"]),
            "result": _loads(row["result_json"]),
            "error": row.get("error"), "task_id": row.get("task_id"),
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }
        if with_steps:
            view["steps"] = [dict(s) for s in dao.list_steps(row["id"])]
        return view

    # ---------- 孤儿扫描（启动时挂 _lifespan，§5.6.3）----------

    @staticmethod
    def scan_orphans() -> int:
        """pending/running 且 updated_at 超 60s → failed（不自动重跑）。

        副作用步骤无法判断是否已执行，宁可标失败交人工——与
        设计文档 §5.6.3 的保守策略一致。返回处置条数。
        """
        from services.db import scoped
        count = 0
        cutoff = (_utcnow()
                  - timedelta(seconds=PlanRunService._ORPHAN_STALE_SEC)
                  ).strftime("%Y-%m-%d %H:%M:%S")
        with scoped() as conn:
            dao = AgentChatDAO(conn)
            audit = AuditDAO(conn)
            rows = dao.list_runs_by_status(("pending", "running"),
                                           updated_before=cutoff)
            for r in rows:
                if dao.transition_status(r["id"], r["status"], "failed",
                                         error="进程重启，执行中断"):
                    audit.insert(r["user_id"], "agent_orphan_failed",
                                 json.dumps({"run_id": r["id"],
                                             "from": r["status"]},
                                            ensure_ascii=False))
                    count += 1
        if count:
            log.warning(f"孤儿认知任务扫描：{count} 条标 failed（不自动重跑）")
        return count
