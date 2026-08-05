"""模型版本注册与切换测试。"""

from dao.db import get_conn, init_db
from services.model_service import ModelService


def test_register_and_switch_model():
    conn = get_conn(":memory:")
    init_db(conn)
    svc = ModelService(conn)
    old_id = svc.register("ppe", "v1", "data/models/old.onnx", mAP50=0.5)
    new_id = svc.register("ppe", "v2", "data/models/new.onnx", mAP50=0.6,
                          active=True)
    assert svc.active_model("ppe")["id"] == new_id

    svc.switch("ppe", old_id)
    assert svc.active_model("ppe")["id"] == old_id
    models = svc.list_models()
    assert len(models) == 2
