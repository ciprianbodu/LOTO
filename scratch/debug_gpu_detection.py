import torch
import subprocess
import os

print(f"Torch version: {torch.__version__}")
print(f"CUDA available: {torch.cuda.is_available()}")

if torch.cuda.is_available():
    try:
        free, total = torch.cuda.mem_get_info()
        print(f"Torch mem info: Free={free/(1024**2):.1f}MB, Total={total/(1024**2):.1f}MB")
    except Exception as e:
        print(f"Torch mem info FAILED: {e}")

try:
    res = subprocess.check_output(
        ["nvidia-smi", "--query-gpu=memory.free,memory.total,gpu_name", "--format=csv,nounits,noheader"],
        stderr=subprocess.STDOUT,
        text=True
    ).strip()
    print(f"nvidia-smi output: {res}")
except Exception as e:
    print(f"nvidia-smi FAILED: {e}")
