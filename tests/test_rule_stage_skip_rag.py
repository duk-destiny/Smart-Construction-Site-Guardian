"""规范 Agent 预检与条款匹配策略测试（不依赖真实 RAG / PDF）。"""

from pipeline.base import StageMessage
from pipeline.rule import RuleStage


class _FakeRag:
    def __init__(self):
        self.queries: list[str] = []

    def query(self, text: str, top_k: int = 5):
        self.queries.append(text)
        return [{
            "clause_no": "一",
            "clause_text": "第一条 与本场景无关的规范条款",
            "score": 0.50,
        }]


def _msg(payload: dict) -> StageMessage:
    return StageMessage(
        task_id="t_rule", agent="rule", status="pending",
        payload=payload, error=None, cost_ms=0)


def test_skip_rag_does_not_query():
    rag = _FakeRag()
    out = RuleStage(rag=rag).run(_msg({
        "permit_info": {"watcher": ""},
        "violation_descs": ["火花"],
        "skip_rag": True,
    }))
    assert out.status == "success"
    assert rag.queries == []
    fields = {c.get("field") for c in out.payload["compliance"]}
    assert "watcher" in fields
    assert "violation" not in fields


def test_no_matched_clause_uses_review_instead_of_first_clause():
    rag = _FakeRag()
    out = RuleStage(rag=rag).run(_msg({
        "permit_info": {"watcher": "张三"},
        "violation_descs": ["火花"],
        "skip_rag": False,
    }))
    assert rag.queries
    violation = next(c for c in out.payload["compliance"]
                     if c.get("field") == "violation")
    assert violation["clause_ref"] == ""
    assert violation["clause_no"] == ""
    assert violation["needs_review"] is True
    assert any("人工复核" in tip for tip in out.payload["training_tips"])
