
import timesfm
import torch

try:
    print(f"TimesFM version: {getattr(timesfm, '__version__', 'unknown')}")
    # Check if XReg or covariates are mentioned in hparams
    hparams = timesfm.TimesFmHparams(context_len=512, horizon_len=1, input_patch_len=32, output_patch_len=128, num_layers=2, model_dims=64, backend="cpu")
    print(f"Hparams: {hparams}")
    # Look for 2.0 features
except Exception as e:
    print(f"Error checking TimesFM: {e}")
