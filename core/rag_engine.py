"""RAG 检索引擎（bge-small-zh + ChromaDB 持久化）。

职责：加载规范 PDF，用 bge 向量化后存入 Chroma，提供语义检索。

线程安全说明（Windows）：
onnxruntime/CUDA 后端无法在非主线程首次初始化。因此 BGE 模型与 Chroma 集合均做成
进程级单例，并由 app 主线程在启动时调用 ``RagEngine.preload()`` 预热一次；此后告警
守护线程复用同一模型对象做 encode，不再在非主线程新建 onnx 会话，规避线程崩溃。
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

from core.pdf_parser import PdfParser
from core.logging import get_logger
log = get_logger(__name__)

# BGE 默认权重路径（与 config.yaml: bge_dir 一致）
_DEFAULT_BGE = "data/models/BAAI--bge-small-zh-v1.5/snapshots/master"

# 进程级单例：BGE 模型（None=未加载 / SentenceTransformer / False=加载失败）
_MODEL = None
_MODEL_LOCK = threading.Lock()

# 进程级单例：Chroma 集合缓存，key = "{chroma_dir}::{collection_name}"
_COLLECTIONS: dict[str, object] = {}
_COLLECTION_LOCK = threading.Lock()
_ENCODE_LOCK = threading.Lock()  # 序列化并发 encode，避免共享单例模型上的竞态


class RagEngine:
    """本地 RAG 引擎：BGE Embedding + ChromaDB 向量检索。"""

    def __init__(self, bge_dir: str | None = None, chroma_dir: str = "data/kb/chroma",
                 collection_name: str = "kb_hot_work"):
        self._bge_dir = bge_dir
        self._chroma_dir = chroma_dir
        self._collection_name = collection_name

    @staticmethod
    def _load_model():
        """懒加载 BGE 模型（进程级单例，所有线程/实例共享）。"""
        global _MODEL
        with _MODEL_LOCK:
            if _MODEL is None:
                try:
                    from sentence_transformers import SentenceTransformer
                    _MODEL = SentenceTransformer(_DEFAULT_BGE)
                except Exception as e:
                    log.warning(f"BGE 加载失败: {e}")
                    _MODEL = False  # type: ignore[assignment]
        return _MODEL if _MODEL is not False else None

    @classmethod
    def preload(cls, collection_name: str = "kb_hot_work",
                chroma_dir: str = "data/kb/chroma",
                bge_dir: str | None = None) -> object | None:
        """主线程预热：加载 BGE 模型 + Chroma 集合，初始化 onnxruntime 后端。

        必须在主线程调用一次（app 启动期），之后守护线程复用单例模型即可正常 encode。
        """
        model = RagEngine._load_model()
        RagEngine._ensure_collection(collection_name, chroma_dir)
        return model

    @staticmethod
    def _ensure_collection(collection_name: str, chroma_dir: str):
        """懒加载/复用 Chroma 集合（进程级缓存，按 chroma_dir+name 隔离）。"""
        key = f"{chroma_dir}::{collection_name}"
        with _COLLECTION_LOCK:
            col = _COLLECTIONS.get(key)
            if col is None:
                try:
                    import chromadb
                    os.makedirs(chroma_dir, exist_ok=True)
                    client = chromadb.PersistentClient(path=chroma_dir)
                    col = client.get_or_create_collection(
                        name=collection_name,
                        metadata={"hnsw:space": "cosine"},
                    )
                    _COLLECTIONS[key] = col
                except Exception as e:
                    log.warning(f"Chroma 加载失败: {e}")
                    _COLLECTIONS[key] = False  # type: ignore[assignment]
                    col = False  # type: ignore[assignment]
            return col if col is not False else None

    def _get_collection(self):
        """复用进程级 Chroma 集合缓存。"""
        return RagEngine._ensure_collection(self._collection_name, self._chroma_dir)

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

        # 清空旧集合并重新写入：新建独立 client，规避旧版 chromadb Collection 无 .client 属性
        # （旧实现 col.client 抛 AttributeError 被吞掉，导致重复 build 变成追加而非覆盖）
        import chromadb
        client = chromadb.PersistentClient(path=self._chroma_dir)
        try:
            client.delete_collection(self._collection_name)
        except Exception:
            pass
        col = client.get_or_create_collection(
            name=self._collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        # 同步更新进程级缓存，避免 query 复用已删除的旧集合
        _COLLECTIONS[f"{self._chroma_dir}::{self._collection_name}"] = col

        # 批量向量化
        ids: list[str] = []
        embeddings: list[list[float]] = []
        metadatas: list[dict[str, str]] = []
        documents: list[str] = []

        for i, c in enumerate(all_clauses):
            with _ENCODE_LOCK:
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
            with _ENCODE_LOCK:
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