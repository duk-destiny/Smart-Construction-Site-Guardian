"""RAG 检索引擎（bge-small-zh + ChromaDB 持久化）。
    
职责：加载规范 PDF，用 bge 向量化后存入 Chroma，提供语义检索。
"""
from __future__ import annotations

import os
from pathlib import Path

from core.pdf_parser import PdfParser
from core.logging import get_logger
log = get_logger(__name__)


class RagEngine:
    """本地 RAG 引擎：BGE Embedding + ChromaDB 向量检索。"""

    def __init__(self, bge_dir: str | None = None, chroma_dir: str = "data/kb/chroma",
                 collection_name: str = "kb_hot_work"):
        self._bge_dir = bge_dir
        self._chroma_dir = chroma_dir
        self._collection_name = collection_name
        self._model = None
        self._collection = None

    def _load_model(self):
        """懒加载 BGE 模型，避免 import 阻塞。"""
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
                model_path = self._bge_dir or "data/models/BAAI--bge-small-zh-v1.5/snapshots/master"
                self._model = SentenceTransformer(model_path)
            except Exception as e:
                log.warning(f"BGE 加载失败: {e}")
                self._model = False  # type: ignore[assignment]
        return self._model if self._model is not False else None

    def _get_collection(self):
        """懒加载 Chroma 集合。"""
        if self._collection is None:
            try:
                import chromadb
                os.makedirs(self._chroma_dir, exist_ok=True)
                client = chromadb.PersistentClient(path=self._chroma_dir)
                self._collection = client.get_or_create_collection(
                    name=self._collection_name,
                    metadata={"hnsw:space": "cosine"},
                )
            except Exception as e:
                log.warning(f"Chroma 加载失败: {e}")
                self._collection = False  # type: ignore[assignment]
        return self._collection if self._collection is not False else None

    def build(self, pdf_paths: list[str]) -> int:
        """从 PDF 列表构建知识库，返回入库条款数。
        
        幂等：重复 build 会清空旧数据重新写入。
        """
        model = self._load_model()
        col = self._get_collection()
        if model is None or col is None:
            return 0

        # 解析所有 PDF
        all_clauses: list[dict[str, str]] = []
        for p in pdf_paths:
            all_clauses.extend(PdfParser.parse(p))
        if not all_clauses:
            return 0

        # 清空旧集合并重新写入
        try:
            client = col.client  # type: ignore[union-attr]
            client.delete_collection(self._collection_name)
            col = client.create_collection(
                name=self._collection_name,
                metadata={"hnsw:space": "cosine"},
            )
        except Exception:
            pass

        # 批量向量化
        ids: list[str] = []
        embeddings: list[list[float]] = []
        metadatas: list[dict[str, str]] = []
        documents: list[str] = []

        for i, c in enumerate(all_clauses):
            emb = model.encode(c["clause_text"], normalize_embeddings=True).tolist()
            ids.append(f"clause_{i}")
            embeddings.append(emb)
            metadatas.append({"clause_no": c["clause_no"]})
            documents.append(c["clause_text"])

        if embeddings:
            col.add(ids=ids, embeddings=embeddings, metadatas=metadatas, documents=documents)
        return len(all_clauses)

    def query(self, text: str, top_k: int = 3) -> list[dict]:
        """语义检索，返回 top_k 条匹配条款。
        
        Returns: [{"clause_no", "clause_text", "score"}, ...]
        """
        model = self._load_model()
        col = self._get_collection()
        if model is None or col is None:
            return []

        try:
            query_emb = model.encode(text, normalize_embeddings=True).tolist()
            results = col.query(query_embeddings=[query_emb], n_results=top_k)
            out: list[dict] = []
            if results["ids"] and results["ids"][0]:
                for i in range(len(results["ids"][0])):
                    score = 1.0 - results.get("distances", [[1.0]])[0][i]
                    clause_no = ""
                    if results.get("metadatas") and results["metadatas"][0]:
                        clause_no = results["metadatas"][0][i].get("clause_no", "")
                    out.append({
                        "clause_no": clause_no,
                        "clause_text": results["documents"][0][i] if results.get("documents") else "",
                        "score": round(float(score), 4),
                    })
            return out
        except Exception as e:
            log.warning(f"查询失败: {e}")
            return []
