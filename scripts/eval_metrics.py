# -*- coding: utf-8 -*-
"""端到端评测：检测推理延迟 + RAG 召回率/延迟。

用法：python scripts/eval_metrics.py
输出：终端 Markdown 表格 + data/eval/metrics.json
"""
from __future__ import annotations

import json
import os
import sys
import time

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("TQDM_DISABLE", "1")
sys.path.insert(0, os.getcwd())

import numpy as np

OUT = {"detection": {}, "rag": {}}


def eval_detection():
    """单帧推理延迟：火情头 / PPE 头 / 双头并行。"""
    import cv2
    from core.yolo_engine import YoloEngine
    from core.config import ConfigLoader

    cfg = ConfigLoader()
    img_path = "data/uploads/t_031dcfb623cb_fire1_mp4-26_jpg.rf.5a09c11c9facf23a9413ca63bc2a6085.jpg"
    if not os.path.exists(img_path):
        import glob
        fs = glob.glob("data/uploads/*fire1*.jpg")
        img_path = fs[0] if fs else None
    frame = cv2.imread(img_path) if img_path else np.zeros((640, 640, 3), np.uint8)

    fire = YoloEngine(conf_thres=0.35, iou_thres=0.45,
                      class_map={"spark": "spark", "smoke": "smoke", "extinguisher": "extinguisher"})
    fire.load("data/models/yolov8_fire_smoke_v2.onnx", intra_op_threads=2)
    ppe = YoloEngine(conf_thres=0.25, iou_thres=0.45,
                     class_map={"helmet": "helmet", "no_helmet": "no_helmet",
                                "vest": "vest", "no_vest": "no_vest", "person": "person"})
    ppe.load("data/models/ppe_yolov8_v2.onnx", intra_op_threads=2)

    def bench(fn, n=20):
        fn()  # warmup
        t0 = time.perf_counter()
        for _ in range(n):
            fn()
        return int((time.perf_counter() - t0) / n * 1000)

    fire_ms = bench(lambda: fire.infer_frame(frame))
    ppe_ms = bench(lambda: ppe.infer_frame(frame))

    # 双头并行（模拟 realtime_engine.detect 的 ThreadPoolExecutor 路径）
    from concurrent.futures import ThreadPoolExecutor
    def both():
        with ThreadPoolExecutor(max_workers=2) as ex:
            f1 = ex.submit(fire.infer_frame, frame)
            f2 = ex.submit(ppe.infer_frame, frame)
            f1.result(); f2.result()
    both_ms = bench(both)

    OUT["detection"] = {
        "fire_ms_per_frame": fire_ms,
        "ppe_ms_per_frame": ppe_ms,
        "dual_parallel_ms_per_frame": both_ms,
        "serial_sum_ms": fire_ms + ppe_ms,
    }
    print(f"| 检测头 | 单帧延迟 |")
    print(f"| --- | ---: |")
    print(f"| 火情 YOLOv8 | {fire_ms} ms |")
    print(f"| PPE YOLOv8 | {ppe_ms} ms |")
    print(f"| 双头并行（取最大） | {both_ms} ms |")
    print(f"| 双头串行（求和） | {fire_ms + ppe_ms} ms |")
    print()


def eval_rag():
    """RAG 召回率@5 + 查询延迟。"""
    from core.rag_engine import RagEngine
    import chromadb

    RagEngine.preload()
    client = chromadb.PersistentClient(path="data/kb/chroma")
    cols = [c.name for c in client.list_collections()]
    if "kb_hot_work" not in cols:
        print("kb_hot_work 集合不存在，跳过 RAG 评测")
        return
    col = client.get_collection("kb_hot_work")
    all_data = col.get()
    docs = all_data.get("documents", [])
    ids = all_data.get("ids", [])
    total = len(docs)
    if total == 0:
        print("知识库为空，跳过 RAG 评测")
        return

    rag = RagEngine(collection_name="kb_hot_work")
    # 召回率@5：用每个 chunk 前 24 字作查询，检查该 chunk 是否在 top-5
    hits = 0
    latencies = []
    sample = docs if total <= 60 else docs[:60]
    for doc, cid in zip(sample, ids[:len(sample)]):
        q = (doc or "").strip().replace("\n", "")[:24]
        if len(q) < 4:
            continue
        t0 = time.perf_counter()
        res = rag.query(q, top_k=5)
        latencies.append((time.perf_counter() - t0) * 1000)
        retrieved_texts = [r["clause_text"] for r in res]
        key = (doc or "")[:30]
        if key and any(key in rt for rt in retrieved_texts):
            hits += 1
    queried = len(latencies)
    recall = round(hits / queried, 3) if queried else 0
    avg_ms = round(sum(latencies) / queried, 1) if queried else 0

    # 人工领域查询样例
    samples = ["动火作业必须设置监火人", "灭火器配置要求", "防火毯设置", "受限空间动火检测可燃气体"]
    sample_results = []
    for q in samples:
        res = rag.query(q, top_k=2)
        sample_results.append({"query": q, "top1_score": res[0]["score"] if res else 0,
                               "top1_text": (res[0]["clause_text"][:50] if res else "")})

    OUT["rag"] = {
        "kb_chunks": total,
        "recall_at_5": recall,
        "avg_query_ms": avg_ms,
        "queried": queried,
        "samples": sample_results,
    }
    print(f"| RAG 指标 | 值 |")
    print(f"| --- | ---: |")
    print(f"| 知识库条目数 | {total} |")
    print(f"| 召回率@5（chunk 派生查询） | {recall} |")
    print(f"| 平均查询延迟 | {avg_ms} ms |")
    print()
    print("领域查询样例：")
    for s in sample_results:
        print(f"- 「{s['query']}」→ top1 score={s['top1_score']} ｜ {s['top1_text']}…")
    print()


if __name__ == "__main__":
    try:
        print("## 检测推理延迟\n")
        eval_detection()
    except Exception as e:
        print(f"检测评测失败：{type(e).__name__}: {e}")
    try:
        print("## RAG 检索评测\n")
        eval_rag()
    except Exception as e:
        print(f"RAG 评测失败：{type(e).__name__}: {e}")
    os.makedirs("data/eval", exist_ok=True)
    with open("data/eval/metrics.json", "w", encoding="utf-8") as f:
        json.dump(OUT, f, ensure_ascii=False, indent=2)
    print(f"\n已写入 data/eval/metrics.json")