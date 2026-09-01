"""剧本注册（设计文档 §5.9/§6）：意图 → {规划提示, 汇总 prompt,
键路径回溯校验, 规则模板档兜底}。

- 规划提示：供规划阶段拼入，引导 LLM 选用剧本指定的确定性工具；
- 汇总 prompt（synth_prompt）：剧本专属汇总系统提示（周报含强制
  溯源约束）；
- 校验钩子（validate_fn）：代码侧从汇总正文提取「数字 +（来源：键路径）」
  标注，对照 `gather()` 返回字典断言一致——不达标由内核自动降级重写一次，
  二次仍不达标落模板档记 degraded（§5.9「数据引用可回溯率 100%」验收指标）；
- 规则模板档（template_fn）：纯字符串函数，零 LLM 依赖——LLM 全败时
  按固定剧本作答（降级矩阵第 3 档，§6）。周报模板档直接用 `gather()`
  数据填空，不依赖模型。

注册表是进程内字典，`register_playbook()` 幂等覆盖；
内核不依赖本模块存在（无剧本时走通用规划）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Playbook:
    """一个意图的剧本：规划提示 + 汇总 prompt + 校验钩子 + 模板档兜底。

    synth_prompt / stats_fn / validate_fn 同时非空时，内核汇总阶段走
    剧本专属流程（校验 + 重写一次 + 模板档降级）；否则走通用汇总。
    """

    intent: str
    system_prompt: str                                  # 规划阶段拼入
    template_fn: Callable[[str], str]                   # (user_input) → 模板答案
    deadline_sec: float = 30.0                          # 剧本级墙钟预算
    synth_prompt: str = ""                              # 剧本专属汇总 system prompt
    # (ctx.steps) → 统计字典（剧本校验的事实源）；抛异常/返空 → 落模板档
    stats_fn: Callable[[list], dict] | None = None
    # (answer, stats) → (是否合格, 错误明细列表)
    validate_fn: Callable[[str, dict], tuple[bool, list[str]]] | None = None
    # (ctx) → 计划 dict（确定性预置计划）：非空时内核跳过 LLM 规划直接执行
    # （v2.2：思考型模型对结构化 JSON 输出不稳，已知意图由代码出计划最稳）
    plan_fn: Callable | None = None


_PLAYBOOKS: dict[str, Playbook] = {}


def register_playbook(intent: str, system_prompt: str,
                      template_fn: Callable[[str], str],
                      deadline_sec: float = 30.0,
                      synth_prompt: str = "",
                      stats_fn: Callable[[list], dict] | None = None,
                      validate_fn: Callable[[str, dict],
                                            tuple[bool, list[str]]] | None = None,
                      plan_fn: Callable | None = None,
                      ) -> None:
    """注册剧本（幂等覆盖）。"""
    _PLAYBOOKS[intent] = Playbook(intent, system_prompt, template_fn,
                                  deadline_sec, synth_prompt, stats_fn,
                                  validate_fn, plan_fn)


def get_playbook(intent: str | None) -> Playbook | None:
    return _PLAYBOOKS.get(intent or "")


def list_playbooks() -> list[str]:
    return sorted(_PLAYBOOKS)


# ---------- 意图规则检测（零 LLM：封闭关键词即路由，§5.11 规则层）----------

_WEEKLY_HINT_RE = re.compile(r"周报|周总结|周报告|周安全|weekly", re.I)


def detect_intent(text: str) -> str | None:
    """规则层意图检测：命中封闭关键词即返回剧本意图，否则 None。"""
    if text and _WEEKLY_HINT_RE.search(text):
        return "weekly_report"
    return None


# ---------- 通用规则模板档（降级矩阵第 3 档，零 LLM）----------

def default_template_answer(user_input: str) -> str:
    """通用模板档：LLM 全败且无专属剧本时的可读兜底（§6）。"""
    return ("当前智能分析通道暂时不可用（已降级到规则模板档）。"
            "您可以：① 前往『历史研判』页面直接浏览工单与风险；"
            "② 在『风险周报』区块查看确定性统计；"
            "③ 稍后重试对话。")


# ---------- 周报剧本（§5.9）：统计事实源与键路径回溯 ----------

def fetch_weekly_stats(start: str | None = None,
                       end: str | None = None) -> dict:
    """确定性统计事实源：薄封装 `WeeklyReportService.gather()`（零改动复用）。"""
    from services.db import scoped
    from services.report_service import WeeklyReportService
    with scoped() as conn:
        return WeeklyReportService(conn).gather(start, end)


def _weekly_stats_from_steps(steps: list) -> dict:
    """从执行步骤提取最后一次成功的 weekly_report_data 入参，取同口径统计。"""
    start = end = None
    for s in steps or []:
        if getattr(s, "tool", "") == "weekly_report_data" \
                and getattr(s, "status", "") == "success":
            args = getattr(s, "args", None) or {}
            start, end = args.get("start"), args.get("end")
    return fetch_weekly_stats(start, end)


def flatten_stats(stats: dict) -> dict:
    """`gather()` 字典 → 扁平「键路径 → 值」映射（校验与回溯的事实表）。

    例：orders_by_status.closed、top_classes[0].count、per_assignee[0].overdue_rate。
    """
    flat: dict = {}

    def _walk(prefix: str, val) -> None:
        if isinstance(val, dict):
            for k, v in val.items():
                _walk(f"{prefix}.{k}" if prefix else str(k), v)
        elif isinstance(val, list):
            for i, v in enumerate(val):
                _walk(f"{prefix}[{i}]", v)
        else:
            flat[prefix] = val

    _walk("", stats)
    return flat


# 数字+来源标注对：如 `5（来源：orders_by_status.closed）`（可带 % 号；
# 键路径字符集含中文，便于检出 LLM 自造的非法中文键路径并给出精确报错）
_CITED_RE = re.compile(
    r"(-?\d+(?:\.\d+)?)\s*([%％])?\s*（来源：\s*([\w.\[\]-]+)\s*）")
_DATE_RE = re.compile(r"\d{4}-\d{1,2}-\d{1,2}|\d{4}年\d{1,2}月\d{1,2}日")
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def extract_weekly_citations(answer: str) -> list[tuple[str, str, bool]]:
    """提取正文全部「数字, 键路径, 是否百分号」标注三元组。"""
    return [(m.group(1), m.group(3), m.group(2) is not None)
            for m in _CITED_RE.finditer(answer)]


def traceback_rate(answer: str) -> float:
    """数据引用可回溯率（§9 验收指标）：正文带来源标注的数字占比。

    日期串不计入数字；无任何数字时记 1.0（空集平凡满足）。
    """
    cited = list(_CITED_RE.finditer(answer))
    chars = list(answer)
    for m in cited:
        for i in range(*m.span()):
            chars[i] = "□"
    masked = _DATE_RE.sub("□□□□", "".join(chars))
    uncited = len(_NUM_RE.findall(masked))
    total = len(cited) + uncited
    return (len(cited) / total) if total else 1.0


def validate_weekly_narrative(answer: str,
                              stats: dict) -> tuple[bool, list[str]]:
    """键路径回溯校验（§5.9）：正文数字 ↔ `gather()` 键路径 100% 可映射。

    规则：
    - 每个数字必须紧随 `（来源：键路径）` 标注（日期串除外）；
    - 标注键路径必须存在于 `gather()` 扁平键表，且数值一致
      （带 % 号的标注允许 = 字段值×100 的百分展示）。
    返回 (是否合格, 错误明细)。
    """
    errors: list[str] = []
    flat = flatten_stats(stats or {})
    citations = list(_CITED_RE.finditer(answer))

    # 掩掉标注区（含键路径内的索引数字）与日期串后，剩余数字即"漏标注"
    chars = list(answer)
    for m in citations:
        for i in range(*m.span()):
            chars[i] = "□"
    masked = _DATE_RE.sub("□□□□", "".join(chars))
    for m in _NUM_RE.finditer(masked):
        errors.append(f"正文数字 {m.group()} 未标注来源键路径")

    if not citations:
        errors.append("正文未发现任何「数字（来源：键路径）」标注")
    for num_s, path, has_pct in extract_weekly_citations(answer):
        if path not in flat:
            errors.append(f"标注键路径不存在于 gather() 返回: {path}")
            continue
        raw = flat[path]
        try:
            val = float(raw)
        except (TypeError, ValueError):
            errors.append(f"键路径 {path} 非数值（实际: {raw!r}），不可用于数字标注")
            continue
        target = val * 100 if has_pct else val
        tol = 0.051 if has_pct else 1e-6   # 百分展示容忍四舍五入误差
        if abs(float(num_s) - target) > tol:
            errors.append(f"键路径 {path} 数值不符: 正文 {num_s} ≠ 实际 {raw}")
    return (not errors, errors)


WEEKLY_SYNTH_PROMPT = """你是「智护工地」安全周报的叙述生成器。你将收到确定性统计工具 \
weekly_report_data（WeeklyReportService.gather()）返回的结构化统计 JSON，\
请仅基于这些数据用中文撰写安全周报正文。

强制约束（代码侧会做键路径回溯校验，不达标将被驳回重写）：
1. 正文中出现的每个数字（含百分数）必须紧随标注（来源：键路径），\
键路径取自数据 JSON 的键路径，如 orders_by_status.closed、top_classes[0].count；
2. 数字必须原样取自数据：禁止编造、禁止四舍五入、禁止写自行计算的合计或比值；\
需要百分比时引用既有 *_rate 字段并乘以 100 后加 % 标注；
3. 不得出现日期、工单编号等数字（统计周期可原样引用 start/end 字符串）；
4. 结构分「检测概况」「告警与工单闭环」「重点事项」三段，总长 ≤300 字；
5. 只输出周报正文，不要输出任何解释、标题编号以外的多余字符。"""


def weekly_template_answer(user_input: str) -> str:
    """周报剧本规则模板档（降级矩阵第 3 档）：用 `gather()` 数据填空，
    零 LLM；每个数字同样携带（来源：键路径）标注，满足回溯校验。
    统计查询失败时退化为指引文案（不阻断降级链）。
    """
    try:
        stats = fetch_weekly_stats()
    except Exception:  # noqa: BLE001 模板档不得因查询失败而抛错
        return ("周报智能生成暂不可用（降级模板档）。"
                "可在『风险周报』区块手动生成确定性周报；"
                "统计数据为 SQL 聚合，不受 LLM 降级影响。")
    ob = stats["orders_by_status"]
    lines = [f"【安全周报 · 规则模板档】统计周期：{stats['start']} ~ {stats['end']}"]
    lines.append(
        f"检测概况：检测帧总数 {stats['frames']}（来源：frames），"
        f"其中不合规 {stats['bad']}（来源：bad）、"
        f"警告 {stats['warn']}（来源：warn）、合规 {stats['ok']}（来源：ok）。")
    tops = stats.get("top_classes") or []
    if tops:
        lines.append(
            f"最高频隐患类别 {tops[0]['cls']}，命中 {tops[0]['count']}"
            "（来源：top_classes[0].count）次，建议针对性交底与巡查加密。")
    lines.append(
        f"工单闭环：新增工单 {stats['orders_total']}（来源：orders_total）张，"
        f"待整改 {ob['open']}（来源：orders_by_status.open）、"
        f"待验收 {ob['submitted']}（来源：orders_by_status.submitted）、"
        f"已销项 {ob['closed']}（来源：orders_by_status.closed）、"
        f"驳回重改 {ob['rejected']}（来源：orders_by_status.rejected）；"
        f"当前存量逾期未整改 {stats['overdue_open_now']}（来源：overdue_open_now）张。")
    return "\n".join(lines)


# ---------- 预注册 ----------

def _weekly_preset_plan(ctx) -> dict:
    """确定性预置计划（v2.2）：本周周报固定调用 weekly_report_data，
    起止由代码按今天计算——规划零 LLM，杜绝日期幻觉与空计划。"""
    from datetime import date, timedelta
    today = date.today()
    start = today - timedelta(days=today.weekday())   # 本周一
    end = start + timedelta(days=6)                   # 本周日
    # 用户显式点名的周期优先级低（示例场景），默认固定本周
    return {"goal": "生成本周安全周报（统计+解读）",
            "steps": [{"tool": "weekly_report_data",
                       "args": {"start": start.isoformat(),
                                "end": end.isoformat()},
                       "reason": "取本周确定性统计"}]}


register_playbook(
    intent="weekly_report",
    system_prompt=("用户需要安全周报。规划时第一步调用 weekly_report_data "
                   "取确定性统计（可按用户请求传 start/end），"
                   "不要调用其他统计类工具；数字必须来自统计结果。"),
    template_fn=weekly_template_answer,
    deadline_sec=120.0,   # 云端慢通道下规划+执行+汇总的完整预算（超时强制收敛仍生效）
    synth_prompt=WEEKLY_SYNTH_PROMPT,
    stats_fn=_weekly_stats_from_steps,
    validate_fn=validate_weekly_narrative,
    plan_fn=_weekly_preset_plan)

register_playbook(
    intent="generic",
    system_prompt="按用户请求规划最少的只读工具步骤，谨慎使用副作用工具。",
    template_fn=default_template_answer,
    deadline_sec=30.0)
