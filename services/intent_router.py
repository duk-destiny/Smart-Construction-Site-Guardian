"""只读意图路由（v0.5，P3 对话式查进度）。

四层防线中本模块承担第 1/2/3 层：
- 第 1 层规则：工单号/状态词/时间窗/类别词四类封闭模式抽参；
- 第 2 层 LLM：规则无把握且本地 Ollama 可用时调一次 `ask_json` 封闭集分类，
  输出白名单校验；未配置/失败自动跳过（静默回退）；
- 第 4 层问人：模糊情形返回 confirm 候选列表由 UI 让用户点选，绝不猜测执行。

硬边界：本路由**只读**——不产生任何写入、不调用任何写服务
（读写硬隔离见方案文档 5.2）；写操作全部走各 Tab 页确认按钮。
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

from dao.models import UserDAO, WorkOrderDAO

# 状态词 → 库内枚举
# 正则化状态词表( 在中文边界不可靠,改包含式正则);未/没 系优先于 已 系
_STATUS_RES: list[tuple[str, str]] = [
    (r"未闭环|没闭环|还没.{0,3}闭环|未销项|没销项", "open"),
    (r"整改中|待整改|未处理|在办", "open"),
    (r"待验收|验收中", "submitted"),
    (r"已?销项|已?经?闭环|已?关闭|已完成", "closed"),
]
# 时间窗：'近N天'/'最近N天'/'上周'→7；默认 7
_DAYS_RE = re.compile(
    r"(?:近|最近|过去|前)\s*(\d{1,3})\s*天"
    r"|(?:近|过去|上|前)\s*([一两二三四五六七八九十]|\d{1,2})\s*周")
_CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
# 工单号：显式哈希形式 #w_xxx / w_xxx / w-xxx；或口语序数「N 号工单 / #N」
_HASH_ID_RE = re.compile(r"(?:#|(?<![A-Za-z0-9]))w[_\-]?([A-Za-z0-9]{4,16})\b")
_NUM_ID_RE = re.compile(
    r"(?<![0-9.])(\d{1,6})\s*号(?![0-9楼]|层|栋|单元|室|\-)"
    r"|(?:第)(\d{1,6})\s*[张条]"
    r"|#(\d{1,6})\s*号?工单")
_QUERY_HINT_RE = re.compile(r"查|进度|状态|多少|几[张单条]|统计|周报")


@dataclass
class RouteResult:
    """路由结论。action ∈ {order_detail, order_list, overdue_stats,
    weekly_stats, unknown}；tier ∈ {rule, llm, human}。"""

    action: str = "unknown"
    tier: str = "rule"
    order_id: str | None = None          # 命中的唯一工单号
    status: str | None = None            # 'open/submitted/closed' 过滤
    days: int = 7                        # 统计窗口
    hint: str | None = None              # 给 UI 的一句解释
    candidates: list[str] = field(default_factory=list)   # 消歧候选


class IntentRouter:
    """只读意图解析与查询执行（全部手写服务方法，LLM 仅分类兜底）。"""

    def __init__(self, conn: sqlite3.Connection, use_llm: bool = True) -> None:
        self.orders = WorkOrderDAO(conn)
        self.users = UserDAO(conn)
        self.conn = conn
        self._use_llm = use_llm

    def _positional_id(self, n: int) -> str | None:
        """口语序数「N 号」→ 第 N 张最新工单的真实 ID（1 起）；越界为 None。"""
        row = self.conn.execute(
            "SELECT id FROM work_orders ORDER BY created_at DESC LIMIT 1 OFFSET ?",
            (max(0, int(n) - 1),)).fetchone()
        return row["id"] if row else None

    @staticmethod
    def extract(text: str) -> dict:
        hash_ids: list[str] = [f"w_{m.group(1)}" for m in _HASH_ID_RE.finditer(
            text or "") if m.group(1)]
        nums: list[int] = []
        for m in _NUM_ID_RE.finditer(text or ""):
            g = next((g for g in m.groups() if g), None)
            if g and int(g) not in nums:
                nums.append(int(g))
        status = next((v for pat, v in _STATUS_RES
                       if re.search(pat, text or "")), None)
        m_days = _DAYS_RE.search(text or "")
        days = 7
        if m_days:
            if m_days.group(1):
                days = int(m_days.group(1))
            else:
                wk = m_days.group(2)
                days = 7 * (_CN_NUM.get(wk, 1) if not wk.isdigit()
                            else int(wk))
        return {"hash_ids": list(dict.fromkeys(hash_ids))[:5],
                "nums": nums[:5],
                "status": status,
                "days": max(1, min(days, 365)),
                "query_hint": bool(_QUERY_HINT_RE.search(text or ""))}

    def _collect_order_refs(self, x: dict) -> tuple[list[str], list[str]]:
        """把抽到的显式 ID 与序数解析成可执行的候选（保留次序去重）。
        返回 (confirmed_ids, ambiguous_nums)，后者为无法定位的序数。"""
        ordered: list[str] = []
        ambiguous: list[str] = []
        for hid in x["hash_ids"]:
            if self.orders.get(hid) is not None:
                ordered.append(hid)
        for n in x["nums"]:
            oid = self._positional_id(n)
            if oid:
                if oid not in ordered:
                    ordered.append(oid)
            elif f"#{n}" not in ambiguous:
                ambiguous.append(f"#{n}")
        return ordered, ambiguous

    # ---------- 主入口 ----------
    def route(self, text: str) -> RouteResult:
        x = self.extract(text)
        hits_queryish = x["hash_ids"] or x["nums"] or x["status"] or x["query_hint"]
        confirmed, amb_num = self._collect_order_refs(x)

        if amb_num:
            return RouteResult("unknown", tier="human", candidates=[],
                               hint=f"{'、'.join(amb_num)} 超出现有工单范围，"
                                    "可说『最新列表』查看全部。")

        if len(confirmed) == 1:
            return RouteResult("order_detail", tier="rule",
                               order_id=confirmed[0], status=x["status"],
                               hint=f"定位到工单 {confirmed[0]}")

        if len(confirmed) > 1:
            return RouteResult("confirm_list", tier="human",
                               candidates=confirmed, status=x["status"],
                               hint="匹配到多个工单，请选择")

        # 统计类关键词
        if re.search(r"逾期|超期|拖期", text or ""):
            return RouteResult("overdue_stats", tier="rule",
                               hint="当前存量逾期工单如下")
        if re.search(r"统计|周报|汇总|整体情况|概况", text or ""):
            return RouteResult("weekly_stats", tier="rule", days=x["days"],
                               hint=f"近 {x['days']} 天安全概览")

        # 有查询意图但无具体参数 → 最新待办清单/消歧
        if x["status"] or x["query_hint"]:
            rows = self.orders.list_by_status(x["status"] or "open", limit=8)
            cands = [r["id"] for r in rows]
            if len(cands) == 1:
                return RouteResult("order_detail", tier="rule",
                                   order_id=cands[0], status=x["status"],
                                   hint="仅一张匹配工单")
            return RouteResult("order_list", tier="rule", status=x["status"],
                               candidates=cands,
                               hint="匹配多条，请选择或收紧条件")

        # ---------- 第 2 层：LLM 兜底（可用才调，白名单校验） ----------
        llm_out = self._ask_llm(text)
        if llm_out:
            intent = llm_out.get("intent")
            rid = llm_out.get("id")
            if intent == "overdue_stats":
                return RouteResult("overdue_stats", tier="llm",
                                   hint=llm_out.get("hint") or "逾期工单概览")
            if intent in ("weekly_stats", "overall_stats"):
                return RouteResult("weekly_stats", tier="llm",
                                   days=int(llm_out.get("days") or 7),
                                   hint="近期安全概览")
            if intent in ("order_status", "order_search"):
                oid = f"w_{rid}" if rid and not str(rid).startswith("w") else rid
                if oid and self.orders.get(str(oid)) is not None:
                    return RouteResult("order_detail", tier="llm",
                                       order_id=str(oid), hint=f"定位到 {oid}")
                if intent == "order_search":
                    return RouteResult("order_list", tier="llm",
                                       candidates=[
                                           r["id"] for r in
                                           self.orders.list_by_status(
                                               "open", limit=8)],
                                       hint="按语义找到以下待办，请选择")

        # ---------- 第 4 层：交给人工 ----------
        return RouteResult(
            "unknown", tier="human",
            hint=("没有明确的工单号或统计词——"
                  "试试『#w_xxx 的进度』『近7天逾期』『本周统计』，"
                  "或直接浏览下方最新工单列表。"))

    def _ask_llm(self, text: str) -> dict | None:
        if not self._use_llm:
            return None
        try:
            from core.llm_engine import LlmEngine
            eng = LlmEngine()
            if not eng.available():
                return None
            out = eng.ask_json(
                f"用户输入:{text!r}\n"
                "仅当其明显是在询问工单/安全数据时分类,否则 intent=null。\n"
                '允许输出形如:\n'
                '{"intent":"order_status","id":"w_123456789"}\n'
                '{"intent":"order_search","id":null}\n'
                '{"intent":"overdue_stats","days":7}\n'
                '{"intent":"weekly_stats","days":7}\n'
                '{"intent":null}')
            if not out:
                return None
            allowed = {"order_status", "order_search", "overdue_stats",
                       "weekly_stats", "overall_stats", None}
            return out if out.get("intent") in allowed else None
        except Exception:  # noqa: BLE001 LLM 层任何异常静默交还人工层
            return None

    # ---------- 只读执行器（UI 渲染的数据供给） ----------
    def detail_view(self, order_id: str) -> dict | None:
        row = self.orders.get(order_id)
        if row is None:
            return None
        name = None
        if row["assignee_id"]:
            u = self.users.get_by_id(row["assignee_id"])
            name = u["username"] if u else None
        return dict(row, assignee_name=name)

    def list_view(self, statuses=("open", "rejected", "submitted"),
                  limit: int = 10) -> list[dict]:
        out = []
        placeholders = ",".join("?" for _ in statuses)
        for r in self.conn.execute(
            f"SELECT * FROM work_orders WHERE status IN ({placeholders}) "
            "ORDER BY created_at DESC LIMIT ?", (*statuses, limit)):
            item = dict(r)
            if r["assignee_id"]:
                u = self.users.get_by_id(r["assignee_id"])
                item["assignee_name"] = u["username"] if u else None
            else:
                item["assignee_name"] = None
            out.append(item)
        return out

    def overdue_rows(self, as_of: str, limit: int = 20) -> list[dict]:
        rows = [dict(r) for r in self.conn.execute(
            "SELECT w.*, u.username AS assignee_name FROM work_orders w "
            "LEFT JOIN users u ON u.id=w.assignee_id "
            "WHERE status='open' AND deadline IS NOT NULL AND deadline < ? "
            "ORDER BY deadline ASC LIMIT ?", (as_of, limit))]
        return rows
