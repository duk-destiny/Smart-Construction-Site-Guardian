"""pytest 公共配置：将项目根目录加入 sys.path，保证 core/agents/services 可被导入。"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


@pytest.fixture(autouse=True)
def _quiet_assist_pool(request, monkeypatch):
    """业务路径不进全局 LLM 辅助池（测试隔离）。

    编排器在 needs_review 时会向全局单线程池（ReviewStage._ASSIST_POOL）
    提交真实 LLM 辅助任务：带云端 key 的环境单次调用可挂 30-60s，把后续
    assist 异步用例堵在队列外（8s 轮询超时即假失败）。故除直接测 assist
    的用例（使用 assist_env 夹具）外，一律桩掉 assist_async；该方法本身
    另有专项用例覆盖，生产路径不受影响。
    """
    if "assist_env" not in request.fixturenames:
        from pipeline.review import ReviewStage
        monkeypatch.setattr(ReviewStage, "assist_async",
                            lambda *a, **k: None, raising=True)
    yield
