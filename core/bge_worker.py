"""BGE 向量模型子进程 worker —— 将 torch 与主进程 onnxruntime 物理隔离。

根因：torch（sentence_transformers/BGE）与 onnxruntime（YOLO 推理）在同一进程内
多线程运行会触发 Windows 原生段错误（无 Python 回溯，进程直接消失）。把 torch 移到
守护线程无效——仍共享进程地址空间。本 worker 以独立进程承载 BGE，从根本上消除冲突。

协议（行式 JSON over stdin/stdout）：
  请求 {"id":N,"action":"encode","texts":["..."],"normalize":bool}
  请求 {"id":N,"action":"ping"}
  响应 {"id":N,"ok":true,"embeddings":[[...]]}   # encode
  响应 {"id":N,"ok":true}                          # ping
  响应 {"id":N,"ok":false,"error":"..."}           # 失败
  启动就绪首行 {"id":0,"ok":true,"action":"ready"}
  stdin EOF -> exit 0
"""
from __future__ import annotations

import json
import os
import sys

# 纯 CPU + 单线程，与主进程保持一致的崩溃抑制配置
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

_DEFAULT_BGE = "data/models/BAAI--bge-small-zh-v1.5/snapshots/master"


def main() -> None:
    try:
        import torch
        torch.set_num_threads(1)
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(_DEFAULT_BGE, device="cpu")
    except Exception as exc:
        # 模型加载失败：逐请求回复错误，主进程据此降级
        for line in sys.stdin:
            try:
                req = json.loads(line)
            except Exception:
                continue
            sys.stdout.write(json.dumps(
                {"id": req.get("id", -1), "ok": False, "error": f"model load failed: {exc}"}
            ) + "\n")
            sys.stdout.flush()
        return

    # 就绪信号：主进程收到后才发 encode 请求
    sys.stdout.write(json.dumps({"id": 0, "ok": True, "action": "ready"}) + "\n")
    sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        rid = req.get("id", -1)
        action = req.get("action", "")
        if action == "ping":
            sys.stdout.write(json.dumps({"id": rid, "ok": True}) + "\n")
        elif action == "encode":
            try:
                import numpy as np
                texts = req.get("texts", [])
                normalize = req.get("normalize", True)
                embs = model.encode(texts, normalize_embeddings=normalize)
                arr = np.asarray(embs)
                if arr.ndim == 1:
                    arr = arr.reshape(1, -1)
                sys.stdout.write(json.dumps(
                    {"id": rid, "ok": True, "embeddings": arr.tolist()}
                ) + "\n")
            except Exception as e:
                sys.stdout.write(json.dumps(
                    {"id": rid, "ok": False, "error": str(e)}
                ) + "\n")
        else:
            sys.stdout.write(json.dumps(
                {"id": rid, "ok": False, "error": f"unknown action: {action}"}
            ) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
