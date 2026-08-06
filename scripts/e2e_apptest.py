# -*- coding: utf-8 -*-
"""E2E UI test via Streamlit AppTest (no browser). Crash-isolated by group."""
import argparse, os, sys, time, json, glob, urllib.request

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("TQDM_DISABLE", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.getcwd())
# 注意：不在主进程 import torch —— torch + onnxruntime 同进程 + AppTest 多线程
# 会触发 onnxruntime 原生段错误（两库 OpenMP 全局线程池冲突）。
# 线程限制靠上面的 OMP_NUM_THREADS=1 环境变量，app.py 脚本线程内自行 set_num_threads。
DBP = "data/app.db"
CAP = "data/mock_capture.jsonl"
RESULTS = []

def rec(name, ok, detail=""):
    RESULTS.append((name, bool(ok), str(detail)[:200]))
    print(("[PASS] " if ok else "[FAIL] ") + name + " :: " + str(detail)[:200], flush=True)

def errs(at):
    out = []
    for e in at.error:
        out.append("ERR:" + str(getattr(e, "value", e)))
    for e in at.exception:
        out.append("EXC:" + str(getattr(e, "value", e)))
    return out

def ssg(at, key, default=None):
    try:
        v = at.session_state[key]
        return v if v is not None else default
    except Exception:
        return default

def seed_tester():
    import sqlite3, bcrypt
    from dao.db import get_conn, init_db
    conn = get_conn(); init_db(conn)
    h = bcrypt.hashpw(b"test123", bcrypt.gensalt()).decode()
    if conn.execute("SELECT 1 FROM users WHERE username='tester'").fetchone():
        conn.execute("UPDATE users SET pwd_hash=?, role='admin' WHERE username='tester'", (h,))
    else:
        conn.execute("INSERT INTO users(id,username,pwd_hash,role,created_at) VALUES(?,?,?,?,datetime('now'))",
                     ("u_e2e","tester",h,"admin"))
    conn.commit()
    from services.auth_service import AuthService
    res = AuthService(conn).login("tester","test123")
    return conn, res["user_id"]

def qcount(table, where=""):
    import sqlite3
    try:
        c = sqlite3.connect(DBP)
        n = c.execute("SELECT COUNT(*) FROM " + table + (" WHERE " + where if where else "")).fetchone()[0]
        c.close(); return n
    except Exception:
        return -1

def caplines():
    try: return sum(1 for _ in open(CAP, encoding="utf-8"))
    except Exception: return 0

def open_page(page, uid, extra=None, timeout=180):
    from streamlit.testing.v1 import AppTest
    os.environ["PAGE"] = page
    at = AppTest.from_file("scripts/_page_runner.py", default_timeout=timeout)
    at.session_state["role"] = "admin"
    at.session_state["username"] = "tester"
    at.session_state["user_id"] = uid
    if extra:
        for k, v in extra.items():
            at.session_state[k] = v
    at.run()
    return at

def click_btn(at, label_kw):
    b = next((b for b in at.button if label_kw in str(b.label)), None)
    if b: b.click().run()
    return b is not None

def preload_bge():
    t0 = time.time()
    from core.rag_engine import RagEngine
    RagEngine.preload()
    return f"{time.time()-t0:.0f}s"

def g_safe():
    conn, uid = seed_tester()
    from streamlit.testing.v1 import AppTest
    # 1 login
    os.environ["PAGE"] = "login"
    at = AppTest.from_file("scripts/_page_runner.py", default_timeout=60)
    at.run()
    at.text_input[0].input("tester"); at.text_input[1].input("test123")
    at.run()
    click_btn(at, "登录")
    rec("登录", ssg(at, "role") == "admin" and any("欢迎" in s.value for s in at.success),
        f"role={ssg(at,'role')} succ={[s.value for s in at.success]}")
    # 2 upload
    fires = glob.glob("data/uploads/*fire1_mp4-26*.jpg")
    at = open_page("upload", uid)
    at.file_uploader[0].upload("fire1.jpg", open(fires[0], "rb").read()).run()
    clicked = click_btn(at, "开始智能研判")
    nav = ssg(at, "_nav_page"); tid = ssg(at, "current_task_id")
    rec("上传建任务+跳转信号", bool(tid) and nav == "agents", f"clicked={clicked} task={tid} nav={nav} err={errs(at)}")
    # 3 history
    at = open_page("history", uid, timeout=90)
    has_filter = any("目标级别" in str(s.label) for s in at.selectbox)
    rec("历史页筛选器", has_filter, f"selectbox={len(at.selectbox)} err={errs(at)}")
    # 4 admin: demo push + model version
    before_cap = caplines()
    before_nl = qcount("notification_logs", "status='sent'")
    at = open_page("admin", uid, timeout=120)
    for t in at.toggle:
        if "演示模式" in str(t.label): t.set_value(True)
    at.run()
    click_btn(at, "发送测试推送")
    sent_after = qcount("notification_logs", "status='sent'")
    push_ok = any("模拟" in s.value for s in at.success)
    rec("管理端演示推送", push_ok and caplines() > before_cap and sent_after > before_nl,
        f"cap {before_cap}->{caplines()} sent {before_nl}->{sent_after} succ={[s.value for s in at.success]}")
    has_ver = any("选择版本" in str(s.label) for s in at.selectbox) or any("一键切换" in str(b.label) for b in at.button)
    rec("管理端模型版本下拉", has_ver, f"err={errs(at)}")
    # 5 LLM smoke
    try:
        body = json.dumps({"model": "qwen3:8b", "stream": False, "think": False,
            "messages": [{"role": "user", "content": "只回复OK两个字"}],
            "options": {"num_predict": 20}}).encode()
        req = urllib.request.Request("http://localhost:11434/api/chat", data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=60) as r:
            msg = (json.loads(r.read().decode()).get("message") or {}).get("content", "")
        rec("LLM(ollama qwen3:8b)", bool(msg.strip()), f"reply={msg.strip()[:40]!r}")
    except Exception as e:
        rec("LLM(ollama qwen3:8b)", False, f"{type(e).__name__}: {e}")

def g_realtime():
    conn, uid = seed_tester()
    at = open_page("realtime", uid, timeout=120)
    for t in at.toggle:
        if "连续监控" in str(t.label): t.set_value(False)
    at.run()
    at.text_area[0].input("demo://").run()
    click_btn(at, "抓取全部源")
    rec("实时页demo源抓取", any("已抓取" in s.value for s in at.success), f"success={[s.value for s in at.success]} err={errs(at)}")

def g_agents():
    conn, uid = seed_tester()
    rec("BGE预加载", True, preload_bge())
    from services.task_service import TaskService
    ts = TaskService(conn)
    permit = {"scene": "hot_work", "fire_level": "二级", "watcher": "张三", "valid_until": "2026-12-31",
              "area": "A区", "extinguisher": "已配备", "fire_blanket": "已设置", "approval": "已审批"}
    tid = ts.create_task(uid, [], permit)
    fires = glob.glob("data/uploads/*fire1_mp4-26*.jpg")
    at = open_page("agents", uid, {"current_task_id": tid, "permit_info": permit, "scene": "hot_work",
        "uploaded_path": fires[0], "_ran": False}, timeout=180)
    click_btn(at, "运行多Agent研判")
    subs = [s.value for s in at.subheader]
    exp_labels = [str(e.label) for e in at.expander]
    has_chain = any("证据链" in l for l in exp_labels)
    cards = all(any(kw in s for s in subs) for kw in ["感知视觉", "闭环处置", "风险融合", "复核"])
    rec("多Agent研判(含LLM润色)", cards and has_chain, f"cards={cards} chain={has_chain} subs={subs[:5]} err={errs(at)}")
    result = ssg(at, "_result")
    before_xlsx = len(glob.glob("data/exports/*.xlsx"))
    at = open_page("report", uid, {"report_result": result, "current_task_id": tid}, timeout=120)
    click_btn(at, "导出 Excel 台账")
    rec("工单导出Excel", any("已导出" in s.value for s in at.success) and len(glob.glob("data/exports/*.xlsx")) > before_xlsx,
        f"succ={[s.value for s in at.success]} xlsx {before_xlsx}->{len(glob.glob('data/exports/*.xlsx'))} err={errs(at)}")
    ri = next((t for t in at.text_input if "改判原因" in str(t.label)), None)
    if ri: ri.input("E2E自测改判").run()
    click_btn(at, "提交改判")
    rec("人工改判", any("改判已记录" in s.value for s in at.success), f"succ={[s.value for s in at.success]} err={errs(at)}")

def g_diag():
    conn, uid = seed_tester()
    rec("BGE预加载", True, preload_bge())
    at = open_page("diag", uid, {"notify_demo": True}, timeout=180)
    click_btn(at, "一键自检")
    md = " ".join(str(m.value) for m in at.markdown)
    passed = "pass" in md
    rec("系统自检(5项全链路)", passed, f"summary={'pass' if passed else 'fail'} err={errs(at)}")

def g_nav():
    from streamlit.testing.v1 import AppTest
    conn, uid = seed_tester()
    # nav test only checks the upload->agents switch; skip BGE(torch)+LLM warmup
    # thread to avoid the AppTest native crash triggered by app.py full combo.
    from core import rag_engine, llm_engine
    rag_engine.RagEngine.preload = classmethod(lambda cls, *a, **k: None)
    llm_engine.LlmEngine.warmup = lambda self: None
    at = AppTest.from_file("app.py", default_timeout=150)
    at.session_state["role"] = "admin"; at.session_state["username"] = "tester"; at.session_state["user_id"] = uid
    at.run()
    fires = glob.glob("data/uploads/*fire1_mp4-26*.jpg")
    at.file_uploader[0].upload("fire1.jpg", open(fires[0], "rb").read()).run()
    fsb = next((b for b in at.button if "开始智能研判" in str(b.label)), None)
    if fsb: fsb.click().run()
    on_agents = any("运行多Agent研判" in str(b.label) for b in at.button) or any("多Agent" in s.value for s in at.subheader)
    rec("页面切换:上传->研判", bool(on_agents), f"buttons={[b.label for b in at.button][:4]} subs={[s.value for s in at.subheader][:3]} err={errs(at)}")

GROUPS = {"safe": g_safe, "realtime": g_realtime, "agents": g_agents, "diag": g_diag, "nav": g_nav}

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--group", required=True); a = ap.parse_args()
    fn = GROUPS.get(a.group)
    if not fn: print(f"unknown group {a.group}"); sys.exit(2)
    try:
        fn()
    except Exception as e:
        import traceback; traceback.print_exc()
        rec(f"[{a.group}] 异常", False, f"{type(e).__name__}: {e}")
    npass = sum(1 for _, ok, _ in RESULTS if ok); ntot = len(RESULTS)
    print(f"\n=== {a.group}: {npass}/{ntot} passed ===", flush=True)
    sys.exit(0 if npass == ntot else 1)