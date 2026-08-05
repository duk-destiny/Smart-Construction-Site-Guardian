import sys
sys.path.insert(0, r"C:\Users\k'k\Desktop\海之子\hzz-fire-safety")
try:
    from sentence_transformers import CrossEncoder
    print("CrossEncoder: OK")
except Exception as e:
    print("CrossEncoder ERR:", e)
try:
    import huggingface_hub
    print("huggingface_hub:", huggingface_hub.__version__)
except Exception as e:
    print("hf_hub ERR:", e)
import os
p = r"data/models/BAAI--bge-reranker-base"
print("reranker dir exists:", os.path.isdir(p))