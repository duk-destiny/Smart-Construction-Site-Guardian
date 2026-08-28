"""RAG 检索引擎（bge-small-zh + ChromaDB 持久化）。

职责：加载规范 PDF，用 bge 向量化后存入 Chroma，提供语义检索。

进程隔离说明（Windows）：
torch（sentence_transformers/BGE）与 onnxruntime（YOLO 推理）在同一进程内多线程
运行会触发原生段错误。因此 BGE 模型运行在独立子进程（core.bge_worker），主进程通过
stdin/stdout JSON 行协议委托 encode，从根本上隔离 torch。Chroma 集合仍为进程级单例。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import atexit
import threading
from pathlib import Path

from core.pdf_parser import PdfParser
from core.logging import get_logger
log = get_logger(__name__)

# BGE 默认权重路径（与 config.yaml: bge_dir 一致）
_DEFAULT_BGE = "data/models/BAAI--bge-small-zh-v1.5/snapshots/master"

# BGE-small-zh 最大 512 token，中文约 1.5-2 token/字；留余量取 400 字为分块上限，
# 重叠 80 字避免语义在边界被截断。
_MAX_CHUNK_CHARS = 400
_CHUNK_OVERLAP = 80

# 进程级单例：BGE 模型（None=未加载 / SentenceTransformer / False=加载失败）
_MODEL = None
_MODEL_LOCK = threading.Lock()

# 进程级单例：Chroma 集合缓存，key = "{chroma_dir}::{collection_name}"
_COLLECTIONS: dict[str, object] = {}
_COLLECTION_LOCK = threading.Lock()
_ENCODE_LOCK = threading.Lock()  # 序列化并发 encode，避免共享单例模型上的竞态


class _BgeProxy:
    """BGE 子进程代理：encode 委托给独立进程，隔离 torch 与主进程 onnxruntime。

    通信协议见 core/bge_worker.py。单文本 -> 1D ndarray；列表 -> 2D ndarray，
    完全兼容 sentence_transformers.SentenceTransformer.encode 的调用约定。
    """

    def __init__(self) -> None:
        env = dict(os.environ)
        env["CUDA_VISIBLE_DEVICES"] = ""
        env["OMP_NUM_THREADS"] = "1"
        env["TOKENIZERS_PARALLELISM"] = "false"
        cwd = str(Path(__file__).resolve().parent.parent)
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "core.bge_worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            cwd=cwd,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self._lock = threading.Lock()
        self._seq = 0
        atexit.register(self._cleanup)
        ready = self._recv(timeout=60)
        if not ready or not ready.get("ok"):
            self._cleanup()
            raise RuntimeError(f"BGE worker 启动失败: {ready}")

    def _recv(self, timeout: float = 30) -> dict | None:
        """读一行 JSON，超时返回 None（Windows 兼容：线程中转，select 不支持 pipe）。"""
        result: list[dict | None] = [None]
        done = threading.Event()

        def _reader():
            try:
                line = self._proc.stdout.readline()
                if line:
                    result[0] = json.loads(line)
            except Exception:
                pass
            finally:
                done.set()

        t = threading.Thread(target=_reader, daemon=True)
        t.start()
        done.wait(timeout=timeout)
        if not done.is_set():
            return None
        return result[0]

    def encode(self, text, normalize_embeddings: bool = True):
        import numpy as np
        single = isinstance(text, str)
        texts = [text] if single else list(text)
        with self._lock:
            if self._proc.poll() is not None:
                raise RuntimeError("BGE worker 已退出")
            self._seq += 1
            req = {"id": self._seq, "action": "encode",
                   "texts": texts, "normalize": normalize_embeddings}
            self._proc.stdin.write(json.dumps(req) + "\n")
            self._proc.stdin.flush()
            resp = self._recv(timeout=30)
        if not resp or not resp.get("ok"):
            raise RuntimeError(f"BGE encode 失败: {resp}")
        arr = np.array(resp["embeddings"], dtype=np.float32)
        return arr[0] if single else arr

    @property
    def alive(self) -> bool:
        return self._proc.poll() is None

    def _cleanup(self) -> None:
        try:
            self._proc.terminate()
            self._proc.wait(timeout=5)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass


class RagEngine:
    """本地 RAG 引擎：BGE Embedding + ChromaDB 向量检索。"""

    def __init__(self, bge_dir: str | None = None, chroma_dir: str = "data/kb/chroma",
                 collection_name: str = "kb_hot_work"):
        self._bge_dir = bge_dir
        self._chroma_dir = chroma_dir
        self._collection_name = collection_name

    @staticmethod
    def _load_model():
        """懒加载 BGE 子进程代理（进程级单例，所有线程/实例共享）。

        加载失败时重置为 None，下次调用重试（避免一次性失败永久降级）。
        """
        global _MODEL
        with _MODEL_LOCK:
            if _MODEL is None or (_MODEL is not False and not _MODEL.alive):
                try:
                    _MODEL = _BgeProxy()
                except Exception as e:
                    log.warning(f"BGE 子进程启动失败（下次重试）: {e}")
                    _MODEL = None  # type: ignore[assignment]
        return _MODEL if _MODEL is not False and _MODEL is not None else None

    @classmethod
    def preload(cls, collection_name: str = "kb_hot_work",
                chroma_dir: str = "data/kb/chroma",
                bge_dir: str | None = None) -> object | None:
        """预热：启动 BGE 子进程 + 加载 Chroma 集合。

        BGE 在独立子进程运行（隔离 torch），可在任意线程安全调用。
        """
        model = RagEngine._load_model()
        RagEngine._ensure_collection(collection_name, chroma_dir)
        return model

    @staticmethod
    def _ensure_collection(collection_name: str, chroma_dir: str):
        """懒加载/复用 Chroma 集合（进程级缓存，按 chroma_dir+name 隔离）。

        加载失败时重置为 None，下次调用重试。
        """
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
                    log.warning(f"Chroma 加载失败（下次重试）: {e}")
            return _COLLECTIONS.get(key)

    def _get_collection(self):
        """复用进程级 Chroma 集合缓存。"""
        return RagEngine._ensure_collection(self._collection_name, self._chroma_dir)

    @staticmethod
    def _chunk_text(text: str) -> list[str]:
        """将超长条款按 _MAX_CHUNK_CHARS 分块，块间重叠 _CHUNK_OVERLAP 字。"""
        if len(text) <= _MAX_CHUNK_CHARS:
            return [text]
        chunks: list[str] = []
        start = 0
        while start < len(text):
            end = min(start + _MAX_CHUNK_CHARS, len(text))
            chunks.append(text[start:end])
            if end >= len(text):
                break
            start = end - _CHUNK_OVERLAP
        return chunks

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

        # 长条款分块：避免 BGE 静默截断超过 512 token 的条款
        expanded: list[tuple[str, str]] = []  # (clause_no, chunk_text)
        for c in all_clauses:
            chunks = self._chunk_text(c["clause_text"])
            for j, chunk in enumerate(chunks):
                no = c["clause_no"] if len(chunks) == 1 else f"{c['clause_no']}_p{j+1}"
                expanded.append((no, chunk))

        texts = [t for _, t in expanded]
        with _ENCODE_LOCK:
            import numpy as _np
            embs = _np.asarray(model.encode(texts, normalize_embeddings=True)).tolist()
        for i, (no, text) in enumerate(expanded):
            ids.append(f"clause_{i}")
            embeddings.append(embs[i])
            metadatas.append({"clause_no": no})
            documents.append(text)

        if embeddings:
            col.add(ids=ids, embeddings=embeddings, metadatas=metadatas, documents=documents)
        return len(all_clauses)

    def add_clauses(self, pdf_paths: list[str]) -> int:
        """增量导入：解析 PDF 并追加到现有集合（不擦除已有数据）。

        与 build() 的区别：build 会 delete_collection 重建，
        适合首次全量构建；add_clauses 仅追加，适合逐份增量导入。
        """
        model = self._load_model()
        col = self._get_collection()
        if model is None or col is None:
            return 0

        all_clauses: list[dict[str, str]] = []
        for p in pdf_paths:
            all_clauses.extend(PdfParser.parse(p))
        if not all_clauses:
            return 0

        # 生成唯一 ID：用现有条目数做偏移，避免与已有 clause_N 冲突
        existing = col.count()
        ids = []
        embeddings = []
        metadatas = []
        documents = []

        expanded: list[tuple[str, str]] = []
        for c in all_clauses:
            chunks = self._chunk_text(c["clause_text"])
            for j, chunk in enumerate(chunks):
                no = c["clause_no"] if len(chunks) == 1 else f"{c['clause_no']}_p{j+1}"
                expanded.append((no, chunk))

        texts = [t for _, t in expanded]
        with _ENCODE_LOCK:
            import numpy as _np
            embs = _np.asarray(model.encode(texts, normalize_embeddings=True)).tolist()
        for i, (no, text) in enumerate(expanded):
            ids.append(f"clause_{existing + i}")
            embeddings.append(embs[i])
            metadatas.append({"clause_no": no})
            documents.append(text)

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