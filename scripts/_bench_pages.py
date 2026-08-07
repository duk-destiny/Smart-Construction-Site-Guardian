# -*- coding: utf-8 -*-
import os, sys, time, re
os.environ["CUDA_VISIBLE_DEVICES"] = ""
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["TQDM_DISABLE"] = "1"
os.environ["HZ_PAGE_TIMER"] = "1"
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.getcwd())
try:
    import torch; torch.set_num_threads(1)
except Exception:
    pass
from streamlit.testing.v1 import AppTest

PAGES = ["upload", "realtime", "agents", "report", "history", "admin", "diag"]

def errs(at):
    out = list(at.error) + list(at.exception)
    return [str(getattr(e,"value",e))[:130] for e in out]

def timer(at):
    for cap in at.caption:
        m = re.search(r"render: ([\d.]+)ms", str(cap.value))
        if m: return float(m.group(1))
    return None

results = []
for page in PAGES:
    os.environ["PAGE"] = page
    t0 = time.perf_counter()
    try:
        at = AppTest.from_file("scripts/_page_runner.py", default_timeout=60)
        at.run()
        wall = (time.perf_counter() - t0) * 1000
        inner = timer(at)
        e = errs(at)
        results.append((page, wall, inner, e))
        print(f"{page:10s} wall={wall:7.0f}ms  inner={inner}  errs={len(e)}")
        for x in e[:1]: print(f"           ! {x}")
    except Exception as ex:
        wall = (time.perf_counter() - t0) * 1000
        results.append((page, wall, None, [str(ex)[:130]]))
        print(f"{page:10s} wall={wall:7.0f}ms  CRASH: {str(ex)[:90]}")

print("\n=== render time ranking ===")
for page, wall, inner, _ in sorted([r for r in results if r[2]], key=lambda r: -r[2]):
    print(f"  {page:10s} render={inner:6.0f}ms  wall={wall:6.0f}ms")
