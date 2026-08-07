# -*- coding: utf-8 -*-
"""Test: can BGE load safely in a daemon thread under new config?"""
import os, sys, time, threading
os.environ["CUDA_VISIBLE_DEVICES"] = ""
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["TQDM_DISABLE"] = "1"
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.getcwd())
try:
    import torch; torch.set_num_threads(1)
except Exception:
    pass

result = {"ok": None, "err": None}

def load_bge():
    try:
        from core.rag_engine import RagEngine
        RagEngine.preload()
        result["ok"] = True
    except Exception as e:
        result["ok"] = False
        result["err"] = str(e)[:200]

print("[main] spawning daemon thread to load BGE...")
t0 = time.perf_counter()
th = threading.Thread(target=load_bge, daemon=True)
th.start()

# poll up to 60s
for i in range(120):
    th.join(timeout=0.5)
    if not th.is_alive():
        break
    if i % 10 == 0:
        print(f"[main] still waiting... {i*0.5:.0f}s, thread alive={th.is_alive()}")

dt = time.perf_counter() - t0
print(f"[main] daemon thread finished in {dt:.1f}s, ok={result['ok']}, err={result['err']}")
print("RESULT:", "THREAD_SAFE" if result["ok"] else "THREAD_UNSAFE")
