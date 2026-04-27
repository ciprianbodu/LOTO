import torch
import sys

print(f"Python: {sys.version}")
print(f"Torch version: {torch.__version__}")
print(f"CUDA available: {torch.cuda.is_available()}")
if not torch.cuda.is_available():
    print("CUDA is NOT available.")
    try:
        import torch.version
        print(f"Torch CUDA version: {torch.version.cuda}")
    except Exception as e:
        print(f"Could not check torch.version.cuda: {e}")
else:
    print(f"CUDA device: {torch.cuda.get_device_name(0)}")
    print(f"CUDA capability: {torch.cuda.get_device_capability(0)}")
