"""评测集一键评测（v0.7）：兑现方案文档 4.3/5.2 的资产化承诺。

数据集（`tests/datasets/*.json`，纳入版本管理）：
- `extraction_cases.json`：30 条隐患描述 → EnhanceEngine 四字段命中率
  （双 Provider 任一可用即跑；均未配置则跳过并明示，白名单校验始终执行）；
- `intent_cases.json`：30 条只读意图 → IntentRouter 规则层准确率
  （use_llm=False，确定性；db 级用例由本脚本按 seed_orders 预置内存库）。

输出：终端逐例明细 + 汇总 JSON 写入 `data/eval/dataset_eval.json`
（merge 保留各 suite 最近一次结果），供答辩直接引用。

用法：
    python scripts/eval_datasets.py                 # 全部
    python scripts/eval_datasets.py --suite intent  # 只跑意图（离线可跑）
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from dao.db import get_conn, init_db
from dao.models import RiskDAO, TaskDAO, UserDAO, WorkOrderDAO

DATASETS = ROOT / "tests" / "datasets"
EVAL_OUT = ROOT / "data" / "eval" / "dataset_eval.json"


def _load(name: str) -> dict:
    return json.loads((DATASETS / name).read_text(encoding="utf-8"))


def score_extraction_case(out: dict | None, expected: dict) -> tuple[bool, str, bool]:
    """双层打分:core(类别+场景+描述非空)硬指标;location 为软指标双向子串。

    返回 (core_ok, why, loc_ok)。位置是模糊字段,只考核不否决——
    汇报口径:accuracy_core 为正式数字,accuracy_loc 单列参考。
    """
    if out is None:
        return False, "引擎未返回结果", False
    if out.get("hazard_key") != expected["hazard_key"]:
        return False, f"类别不符: {out.get('hazard_key')!r}", False
    if out.get("scene_id") != expected["scene_id"]:
        return False, f"场景不符: {out.get('scene_id')!r}", False
    if not (out.get("description") or "").strip():
        return False, "描述为空", False
    exp_loc = expected.get("location")
    got_loc = (out.get("location") or "").strip()
    if not exp_loc:
        return True, "ok(无位置预期)", True
    loc_ok = bool(got_loc) and (exp_loc in got_loc or got_loc in exp_loc)
    return True, f"位置软指标{'✓' if loc_ok else '✗'}", loc_ok


def run_extraction() -> dict:
    """提取命中率评测；双 Provider 均不可用时返回 skipped（0 成本）。"""
    from services.enhance_service import EnhanceEngine
    data = _load("extraction_cases.json")
    cases = data["cases"]
    eng = EnhanceEngine()
    provider = eng.available()
    hits, detail = 0, []
    loc_hits = 0
    if provider:
        for c in cases:
            ok, why, loc_ok = score_extraction_case(
                eng.extract_hazard(c["text"]), c["expected"])
            hits += ok
            loc_hits += loc_ok
            detail.append({"id": c["text"][:24], "ok": ok, "why": why})
        accuracy = round(hits / len(cases), 3)
        accuracy_loc = round(loc_hits / len(cases), 3)
    else:
        accuracy = accuracy_loc = None
    return {"suite": "hazard_extraction", "total": len(cases),
            "hits": hits, "accuracy": accuracy,
            "accuracy_location": accuracy_loc, "provider": provider,
            "skipped": provider is None, "detail": detail}


def seed_intent_db(conn: sqlite3.Connection, seed_orders: list[dict]) -> None:
    """按数据集声明预置固定工单（最新序 1..N），使序数断言确定。"""
    users = UserDAO(conn)
    safety = users.insert("zhangsan", "hashed", "safety")
    lisi = users.insert("lisi", "hashed", "responsible")
    for spec in seed_orders:
        tid = TaskDAO(conn).insert(safety, "{}", "completed", source="upload")
        RiskDAO(conn).insert(tid, "一般", "[]", "[]")
        wid = WorkOrderDAO(conn).insert(
            task_id=tid, hazard_desc=f"评测样本{spec['pos']}", clause=None,
            requirement="整改", risk_level="一般", worker_notice="")
        if spec["status"] == "closed":
            conn.execute("UPDATE work_orders SET status='closed' WHERE id=?",
                         (wid,))
        conn.execute("UPDATE work_orders SET created_at=? WHERE id=?",
                     (spec["created_at"], wid))
        conn.execute("UPDATE tasks SET created_at=? WHERE id=?",
                     (spec["created_at"], tid))
        conn.commit()


def run_intent() -> dict:
    """只读路由准确率（use_llm=False 确定性；内存库预置固定工单）。"""
    from services.intent_router import IntentRouter
    data = _load("intent_cases.json")
    conn = get_conn(":memory:")
    init_db(conn)
    seed_intent_db(conn, data["seed_orders"])
    router = IntentRouter(conn, use_llm=False)

    hits, detail = 0, []
    for c in data["cases"]:
        exp = c["expected"]
        res = router.route(c["text"])
        ok = res.action == exp["action"]
        why = f"action={res.action}" if not ok else "ok"
        if ok and "status" in exp:
            ok = res.status == exp["status"]
            why = f"status={res.status}"
        if ok and "days" in exp:
            ok = res.days == exp["days"]
            why = f"days={res.days}"
        if ok and "order_pos" in exp:
            want = router._positional_id(exp["order_pos"])
            ok = res.order_id == want
            why = f"order_id={res.order_id} want={want}"
        if ok and "candidates" in exp:
            want = {router._positional_id(n) for n in exp["candidates"]}
            ok = set(res.candidates) == want
            why = f"cands={res.candidates}"
        hits += ok
        detail.append({"id": c["text"][:24], "ok": ok, "why": why})
    return {"suite": "intent_routing", "total": len(data["cases"]),
            "hits": hits,
            "accuracy": round(hits / len(data["cases"]), 3),
            "tier": "rule(离线确定性)", "detail": detail}


def main() -> int:
    ap = argparse.ArgumentParser(description="评测集一键命中率评测")
    ap.add_argument("--suite", choices=["intent", "extraction", "all"],
                    default="all")
    args = ap.parse_args()

    results: dict = {}
    if EVAL_OUT.exists():
        try:
            results = json.loads(EVAL_OUT.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            results = {}

    suites = (["intent", "extraction"] if args.suite == "all"
              else [args.suite])
    for name in suites:
        res = run_intent() if name == "intent" else run_extraction()
        results[name] = res
        tag = (f"core {res['hits']}/{res['total']} = {res['accuracy']}"
               + (f"｜位置 {res.get('accuracy_location')}"
                  if res.get("accuracy_location") is not None else "")
               if not res.get("skipped") else "SKIPPED（双 Provider 未配置）")
        print(f"\n=== {name}: {tag} ===")
        for d in res["detail"]:
            mark = "✅" if d["ok"] else "❌"
            print(f"  {mark} {d['id']}  {d['why']}")

    EVAL_OUT.parent.mkdir(parents=True, exist_ok=True)
    EVAL_OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    print(f"\n已写入 {EVAL_OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
