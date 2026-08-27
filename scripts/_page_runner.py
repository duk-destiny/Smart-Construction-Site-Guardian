# -*- coding: utf-8 -*-
"""Generic AppTest page runner: import ui.page_<PAGE> and call render_<PAGE>()."""
import os, sys
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("TQDM_DISABLE", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
sys.path.insert(0, os.getcwd())
import importlib
page = os.environ.get("PAGE", "realtime")
mod = importlib.import_module(f"ui.page_{page}")
fn = getattr(mod, f"render_{page}")
fn()
