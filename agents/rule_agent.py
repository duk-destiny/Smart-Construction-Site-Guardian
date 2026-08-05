"""规范 Agent（M04）：接收作业票信息与违规描述，检索规范并判定合规。
    
职责：
1. 调用 RagEngine 检索相关条款
2. 比对待测项 vs 规范要求 → 生成 compliance 列表
3. 生成 training_tips 培训要点

计时与异常兜底由 AgentBase.run 统一完成（代码规范 §4）。
"""
from __future__ import annotations

from agents.base import AgentBase, AgentMessage
from core.rag_engine import RagEngine

# 软匹配：作业票字段值若出现以下词，视为"缺失/不合规"
MISSING_TOKENS = {"", "无", "未", "否", "未配备", "未设置", "未填写"}


class RuleAgent(AgentBase):
    """规范匹配与合规判定 Agent。"""

    def __init__(self, rag: RagEngine | None = None):
        self._rag = rag or RagEngine()

    @staticmethod
    def _match_clause(vd: str, related: list[dict]) -> tuple[dict | None, bool]:
        """匹配规范条款：优先精确命中，其次高相似度，避免默认套用第一条。"""
        for r in related:
            if vd in r.get("clause_text", ""):
                return r, True
        for r in related:
            if float(r.get("score", 0.0) or 0.0) >= 0.80:
                return r, True
        return None, False

    def _execute(self, msg: AgentMessage) -> AgentMessage:
        permit = msg.payload.get("permit_info", {}) or {}
        violation_descs = msg.payload.get("violation_descs", []) or []
        skip_rag = bool(msg.payload.get("skip_rag", False))

        compliance: list[dict] = []
        training_tips: list[str] = []

        # 1. 作业票关键字段检查
        required_fields = {
            "watcher": "监火人",
            "extinguisher": "灭火器",
            "fire_blanket": "防火毯",
            "approval": "作业审批",
        }
        for field, label in required_fields.items():
            val = permit.get(field, "")
            ok = val not in MISSING_TOKENS
            compliance.append({
                "field": field,
                "label": label,
                "value": val or "未填写",
                "verdict": "合规" if ok else "不合规",
                "clause_ref": "",
                "clause_no": "",
                "clause_text": "",
                "clause_verified": False,
                "needs_review": False,
            })

        # 2. 违规描述 → RAG 检索条款；skip_rag 用于编排器并行阶段的作业票预检
        if skip_rag:
            related: list[dict] = []
        else:
            noncompliant_labels = [
                c.get("label", "") for c in compliance
                if c.get("verdict") == "不合规"
            ]
            query_parts = list(violation_descs)
            if not query_parts and noncompliant_labels:
                query_parts = [f"{label} 不合规" for label in noncompliant_labels]
            query_text = " ".join(query_parts) if query_parts else "动火作业安全规范"
            related = self._rag.query(query_text, top_k=5)

        # 3. 违规项匹配规范条款
        # 视觉 Agent 已检出的违规，直接判为不合规；RAG 仅用于补充条款引用。
        # 未匹配到明确条款时不默认套用第一条，交由人工复核补充依据。
        if not skip_rag:
            for vd in violation_descs:
                matched, verified = self._match_clause(vd, related)
                best_clause = matched.get("clause_no", "") if matched else ""
                clause_text = matched.get("clause_text", "") if matched else ""
                compliance.append({
                    "field": "violation",
                    "label": vd,
                    "value": "",
                    "verdict": "不合规",
                    "clause_ref": best_clause,
                    "clause_no": best_clause,
                    "clause_text": clause_text,
                    "clause_verified": verified,
                    "needs_review": not verified,
                })

        # 4. 培训要点（取 top-3 条款 + 通用提示）
        if related:
            for r in related[:3]:
                training_tips.append(f"[{r['clause_no']}] {r['clause_text'][:80]}")
        violation_items = [c for c in compliance if c.get("field") == "violation"]
        if violation_descs and not any(c.get("clause_verified") for c in violation_items):
            training_tips.append("未检索到可直接引用的规范条款，请人工复核并补充条款依据")
        if permit.get("watcher") in MISSING_TOKENS:
            training_tips.append("作业前必须指定专职监火人，监火人不得擅离职守")
        if permit.get("extinguisher") in MISSING_TOKENS:
            training_tips.append("动火现场必须配备灭火器材（灭火器/防火毯）")

        # 去重展示：同一 label+verdict 只保留一条，避免视觉重复框导致刷屏
        unique_compliance = []
        seen = set()
        for c in compliance:
            key = (c.get("label"), c.get("verdict"))
            if key not in seen:
                seen.add(key)
                unique_compliance.append(c)

        msg.status = "success"
        msg.payload = {
            "compliance": unique_compliance,
            "training_tips": training_tips,
            "input_summary": {
                "permit_info": permit,
                "violation_descs": violation_descs,
                "skip_rag": skip_rag,
            },
        }
        return msg
