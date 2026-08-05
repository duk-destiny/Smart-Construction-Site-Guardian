"""Agent 运行证据链测试：落库、查询与输出摘要。"""

import json

from dao.db import get_conn, init_db
from dao.models import UserDAO
from services.task_service import TaskService


def test_save_and_list_agent_runs():
    conn = get_conn(":memory:")
    init_db(conn)
    uid = UserDAO(conn).insert("demo", "hash", "safety")
    svc = TaskService(conn)
    tid = svc.create_task(uid, [], {"watcher": "张三"})

    svc.save_agent_runs(tid, {
        "vision": {
            "status": "success",
            "cost_ms": 12,
            "payload": {
                "detections": [{"cls": "spark", "conf": 0.91}],
                "input_summary": {"image_paths": ["a.jpg"]},
            },
        },
        "rule": {
            "status": "degraded",
            "cost_ms": 8,
            "payload": {},
            "error": "RAG 超时",
        },
    })

    runs = svc.list_agent_runs(tid)
    assert len(runs) == 2
    assert {r["agent"] for r in runs} == {"vision", "rule"}
    vision = next(r for r in runs if r["agent"] == "vision")
    output = json.loads(vision["output_json"])
    assert output["detections"][0]["cls"] == "spark"
    input_summary = json.loads(vision["input_json"])
    assert input_summary["image_paths"] == ["a.jpg"]
    rule = next(r for r in runs if r["agent"] == "rule")
    assert rule["error"] == "RAG 超时"
