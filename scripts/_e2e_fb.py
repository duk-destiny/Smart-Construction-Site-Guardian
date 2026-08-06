# -*- coding: utf-8 -*-
"""端到端验证纠偏闭环：改判→审核confirmed→生成YOLO→并入combined。"""
import os, sys, shutil, glob
os.environ.setdefault("CUDA_VISIBLE_DEVICES","")
os.environ.setdefault("OMP_NUM_THREADS","1")
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.getcwd())

import bcrypt
from dao.db import get_conn, init_db
from services.auth_service import AuthService
from services.task_service import TaskService

conn = get_conn(); init_db(conn)
h = bcrypt.hashpw(b"admin123", bcrypt.gensalt()).decode()
conn.execute("UPDATE users SET pwd_hash=? WHERE username='admin'", (h,)); conn.commit()
uid = AuthService(conn).login("admin","admin123")["user_id"]
ts = TaskService(conn)

# 用一张真实 fire 图建任务，伪造一条 spark 检测，改判→存反馈样本
tid = ts.create_task(uid, [], {"scene":"hot_work","watcher":"张三","extinguisher":"已配备","fire_blanket":"已设置","approval":"已审批"})
fires = glob.glob("data/uploads/*fire1_mp4-26*.jpg")
img = fires[0]
dets = [{"cls":"spark","conf":0.5,"bbox":[0.1,0.1,0.2,0.2]}]
corrections = [{"corrected_cls":"spark","is_fp":False}]
ts.save_feedback_sample(tid, uid, "重大", "E2E闭环验证", auto_level="一般",
    image_path=img, detections=dets, corrected_labels=corrections)

fb = [s for s in ts.list_feedback_samples() if s["task_id"]==tid][-1]
print(f"[1] 反馈样本 id={fb['id']} status={fb['status']}")
# 审核 confirmed
ts.review_feedback_sample(fb["id"], "confirmed", user_id=uid)
fb2 = [s for s in ts.list_feedback_samples() if s["id"]==fb["id"]][0]
print(f"[2] 审核后 status={fb2['status']}")

# 清旧的 feedback_training + combined，跑 build_feedback_dataset
ft = "data/feedback_training/yolo"
if os.path.isdir(ft): shutil.rmtree(ft)
shutil.rmtree("data/combined", ignore_errors=True)

rc = os.system(f'"{sys.executable}" scripts/build_feedback_dataset.py > data/fb_build.log 2>&1')
print(f"[3] build_feedback_dataset rc={rc}")
# 检查 feedback_training 里有图
fb_imgs = glob.glob("data/feedback_training/yolo/fire/images/**/*.jpg", recursive=True)
print(f"    feedback_training fire 图片数={len(fb_imgs)}")
if fb_imgs:
    print(f"    样本: {os.path.basename(fb_imgs[0])}")

# 跑 prepare_combined_dataset，检查 fb_ 前缀图进 combined
rc2 = os.system(f'"{sys.executable}" scripts/prepare_combined_dataset.py > data/fb_prep.log 2>&1')
print(f"[4] prepare_combined_dataset rc={rc2}")
combined_fb = glob.glob("data/combined/fire/train/images/fb_fire_*.jpg")
print(f"    combined/fire/train 里 fb_ 前缀图数={len(combined_fb)}")
combined_labels = glob.glob("data/combined/fire/train/labels/fb_fire_*.txt")
print(f"    combined/fire/train 里 fb_ 标注数={len(combined_labels)}")
if combined_labels:
    print(f"    标注内容: {open(combined_labels[0]).read().strip()[:60]}")

ok = len(fb_imgs)>0 and len(combined_fb)>0 and len(combined_labels)>0
print(f"\n{'闭环验证 PASSED' if ok else '闭环验证 FAILED'}")
sys.exit(0 if ok else 1)