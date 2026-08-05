"""人工纠偏反馈闭环测试：改判样本落库、查询与 CSV 导出。"""

from dao.db import get_conn, init_db
from dao.models import UserDAO, RiskDAO
from services.task_service import TaskService


def test_feedback_sample_save_and_export():
    conn = get_conn(":memory:")
    init_db(conn)
    uid = UserDAO(conn).insert("reviewer", "hash", "safety")
    svc = TaskService(conn)
    tid = svc.create_task(uid, [], {"watcher": "张三"})

    # 先插入自动风险，模拟 Agent 输出
    risk_id = RiskDAO(conn).insert(tid, "重大", '["检测到烟雾"]', "[]")
    assert risk_id

    fid = svc.save_feedback_sample(
        task_id=tid,
        user_id=uid,
        corrected_level="一般",
        reason="现场复核后确认烟雾为背景误报",
        auto_level="重大",
        source_json={"reasons_json": '["检测到烟雾"]'},
        image_path="data/uploads/demo.jpg",
        detections=[{"cls": "smoke", "conf": 0.88, "bbox": [0.5, 0.5, 0.2, 0.2]}],
        corrected_labels=[{"risk_level": "一般"}],
    )
    assert fid

    samples = svc.list_feedback_samples()
    assert len(samples) == 1
    assert samples[0]["corrected_risk_level"] == "一般"
    assert samples[0]["reason"].startswith("现场复核")
    assert samples[0]["image_path"] == "data/uploads/demo.jpg"
    assert samples[0]["status"] == "pending"

    svc.review_feedback_sample(fid, "confirmed", user_id=uid)
    samples = svc.list_feedback_samples()
    assert samples[0]["status"] == "confirmed"

    csv_text = svc.feedback_csv()
    assert "corrected_risk_level" in csv_text
    assert "一般" in csv_text


def test_update_feedback_corrections():
    conn = get_conn(":memory:")
    init_db(conn)
    uid = UserDAO(conn).insert("reviewer2", "hash", "safety")
    svc = TaskService(conn)
    tid = svc.create_task(uid, [], {"watcher": "张三"})
    fid = svc.save_feedback_sample(
        task_id=tid,
        user_id=uid,
        corrected_level="一般",
        reason="逐目标纠偏",
        detections=[{"cls": "smoke", "conf": 0.88, "bbox": [0.5, 0.5, 0.2, 0.2]}],
        corrected_labels=[{"cls": "smoke", "is_fp": False}],
    )
    svc.update_feedback_corrections(
        fid,
        [{"cls": "smoke", "is_fp": True}],
        user_id=uid,
    )
    sample = svc.list_feedback_samples()[0]
    import json
    assert json.loads(sample["corrected_labels_json"])[0]["is_fp"] is True
