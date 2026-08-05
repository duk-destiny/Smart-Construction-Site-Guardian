"""告警生命周期测试：创建、去重、状态流转。"""

from dao.db import get_conn, init_db
from dao.models import UserDAO
from services.task_service import TaskService


def test_alarm_create_dedupe_and_update():
    conn = get_conn(":memory:")
    init_db(conn)
    uid = UserDAO(conn).insert("safety", "hash", "safety")
    svc = TaskService(conn)

    aid = svc.create_alarm_event("s1", None, "hot_work", "spark", 0.91)
    assert aid
    assert svc.create_alarm_event("s1", None, "hot_work", "spark", 0.85) is None

    svc.update_alarm_event(aid, "false_alarm", user_id=uid)
    events = svc.list_alarm_events()
    assert len(events) == 1
    assert events[0]["status"] == "false_alarm"
    assert events[0]["reviewed_by"] == uid
