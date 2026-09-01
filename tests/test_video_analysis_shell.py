"""T6：VideoAnalysisShell（run_video_pipeline 工具）壳逻辑测试。

monkeypatch Orchestrator 避免加载真实模型；断言：
- mode 透传（默认 full / quick）；
- 非法 mode 直接 failed 不起链路；
- 结构化摘要字段（场景/风险等级/证据条数/工单要点）；
- 视频文件内容 hash 回写缓存，第二次调用命中；
- 边界止于 execute 返回（无建单落库动作）。
"""
from __future__ import annotations

import pytest

from pipeline.base import StageMessage
from services.agent import tools as tools_mod
from services.agent.tools import ToolCtx, _tool_run_video_pipeline


class _FakeOrch:
    last: dict = {}

    def __init__(self, vision=None, scene_id=None, **kwargs):
        _FakeOrch.last = {"vision": vision, "scene_id": scene_id, "calls": 0}

    def execute(self, task_id, images=None, video=None, permit_info=None,
                mode="full"):
        _FakeOrch.last["mode"] = mode
        _FakeOrch.last["calls"] = _FakeOrch.last.get("calls", 0) + 1
        return StageMessage(
            task_id=task_id, agent="orchestrator", status="success",
            payload={
                "vision": {"status": "success", "payload": {
                    "detections": [{"cls": "spark", "conf": 0.9}],
                    "violation_descs": ["火花（动火明火）"],
                }},
                "rule": {"status": "success", "payload": {
                    "compliance": [{"verdict": "不合规"}],
                }},
                "fusion": {"status": "success", "payload": {}},
                "review": {"status": "success",
                           "payload": {"needs_review": False}},
                "action": {"status": "success", "payload": {}},
                "risk_level": "重大",
                "reasons": ["检出明火"],
                "work_order": {"risk_level": "重大", "hazard_desc": "明火",
                               "clause": "GB-1", "requirement": "整改"},
            }, error=None, cost_ms=0)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr("pipeline.orchestrator.Orchestrator", _FakeOrch)
    _FakeOrch.last = {}                          # 隔离用例间状态残留
    cache = tools_mod.get_video_cache()
    cache.clear()
    yield
    cache.clear()


def test_shell_default_full_and_summary():
    ctx = ToolCtx(user_id="u1", run_id="run-1")
    out = _tool_run_video_pipeline(
        {"images": [], "permit_info": {"scene": "hot_work"}}, ctx)
    assert out["status"] == "success"
    assert _FakeOrch.last["mode"] == "full"      # 默认 full
    data = out["data"]
    assert data["mode"] == "full"
    assert data["scene_id"] == "hot_work"
    assert data["risk_level"] == "重大"
    assert data["evidence_count"] == {"detections": 1, "compliance": 1}
    assert data["violation_descs"] == ["火花（动火明火）"]
    assert data["work_order_points"]["hazard_desc"] == "明火"
    # 注入缓存的 VisionStage（仅本壳 opt-in）
    assert _FakeOrch.last["vision"].cache is tools_mod.get_video_cache()


def test_shell_quick_mode_passthrough():
    ctx = ToolCtx(user_id="u1", run_id="run-1")
    out = _tool_run_video_pipeline({"mode": "quick"}, ctx)
    assert out["status"] == "success"
    assert _FakeOrch.last["mode"] == "quick"
    assert out["data"]["mode"] == "quick"


def test_shell_invalid_mode_rejected_without_pipeline():
    ctx = ToolCtx(user_id="u1", run_id="run-1")
    out = _tool_run_video_pipeline({"mode": "turbo"}, ctx)
    assert out["status"] == "failed"
    assert "非法执行模式" in out["error"]
    assert _FakeOrch.last.get("calls", 0) == 0   # 未起链路


def test_shell_video_cache_warm_on_second_call(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake-video-bytes")
    ctx = ToolCtx(user_id="u1", run_id="run-1")
    args = {"video": str(video), "permit_info": {"scene": "hot_work"}}
    out1 = _tool_run_video_pipeline(dict(args), ctx)
    assert out1["data"]["cache"]["hits"] == 0    # 首次未命中
    assert out1["data"]["cache"]["size"] >= 1    # 检测结果按视频 hash 回写
    out2 = _tool_run_video_pipeline(dict(args), ctx)
    assert out2["data"]["cache"]["hits"] == 1    # 第二次命中（追问不重跑）
    # 重新上传（新内容）→ 新 key，不命中
    video.write_bytes(b"re-uploaded-bytes")
    out3 = _tool_run_video_pipeline(dict(args), ctx)
    assert out3["data"]["cache"]["hits"] == 0
