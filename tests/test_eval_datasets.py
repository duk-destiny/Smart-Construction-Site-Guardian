"""评测集资产化测试（v0.7）：把方案文档 4.3/5.2 的承诺钉进自动化。

- 数据集完整性：JSON 可解析、id 唯一、提取 expected 键⊆白名单且非 safe、
  场景合法、意图用例 action 封闭；
- 意图路由：use_llm=False 离线全量跑，锁定 100%（回归护栏——路由逻辑任何
  退化都在全量测试中当场暴露）；
- 评分函数：core/location 双层口径单测。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dao.db import get_conn, init_db                       # noqa: E402
from scripts.eval_datasets import (                        # noqa: E402
    run_intent, score_extraction_case, seed_intent_db)

EXTRACT = json.loads((ROOT / "tests/datasets/extraction_cases.json")
                     .read_text(encoding="utf-8"))
INTENT = json.loads((ROOT / "tests/datasets/intent_cases.json")
                    .read_text(encoding="utf-8"))

ACTIONS = {"order_detail", "order_list", "overdue_stats", "weekly_stats",
           "confirm_list", "unknown"}


def test_extraction_dataset_integrity():
    ids = [c["text"] for c in EXTRACT["cases"]]
    assert len(ids) == len(set(ids)) and len(ids) >= 30
    from core.compliance import SEVERITY
    for c in EXTRACT["cases"]:
        key = c["expected"]["hazard_key"]
        assert key in SEVERITY and SEVERITY[key] != "safe", key
        assert c["expected"]["scene_id"] in ("hot_work", "construction_ppe")


def test_intent_dataset_integrity():
    ids = [c["text"] for c in INTENT["cases"]]
    assert len(ids) == len(set(ids)) and len(ids) >= 30
    for c in INTENT["cases"]:
        assert c["expected"]["action"] in ACTIONS, c


def test_intent_offline_full_pass():
    """回归护栏：意图路由离线必须 30/30。任何退化当场红。"""
    conn = get_conn(":memory:")
    init_db(conn)
    seed_intent_db(conn, INTENT["seed_orders"])
    res = run_intent()
    assert res["accuracy"] == 1.0, [
        d for d in res["detail"] if not d["ok"]]


def test_score_extraction_dual_track():
    exp = {"hazard_key": "flammable", "scene_id": "hot_work",
           "location": "3号楼西侧"}
    ok, _, loc = score_extraction_case(
        {"hazard_key": "flammable", "scene_id": "hot_work",
         "description": "x", "location": "位于3号楼西侧拐角"}, exp)
    assert ok and loc                                  # 双向子串
    ok, _, loc = score_extraction_case(
        {"hazard_key": "flammable", "scene_id": "hot_work",
         "description": "x", "location": ""}, exp)
    assert ok and not loc                              # 位置软指标不否决
    ok, why, _ = score_extraction_case(None, exp)
    assert not ok and "未返回" in why
