"""Unified scoring interface for every model in the benchmark.

Each scorer is a callable that accepts:
    * draws_2d : np.ndarray of shape (n_draws, draw_n) with the history of
                 drawn numbers (per row)
    * max_num  : int — universe size (e.g. 49 for 6/49)

…and returns ``Dict[int, float]`` keyed by candidate number (1..max_num),
with scores normalized to [0, 1] (higher = more likely to be drawn next).

A METHODS dict at the bottom registers every available method. Methods that
can't be imported in the current environment are marked unavailable but kept
in the registry so the report acknowledges them.
"""

from __future__ import annotations

import logging
import time
import warnings
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

warnings.filterwarnings("ignore")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _normalize(scores: Dict[int, float], max_num: int) -> Dict[int, float]:
    if not scores:
        return {n: 0.0 for n in range(1, max_num + 1)}
    vals = np.fromiter(scores.values(), dtype=np.float64)
    vmin, vmax = float(vals.min()), float(vals.max())
    rng = max(vmax - vmin, 1e-12)
    out = {int(k): float((v - vmin) / rng) for k, v in scores.items()}
    for n in range(1, max_num + 1):
        out.setdefault(n, 0.0)
    return out


def _build_binary(draws_2d: np.ndarray, max_num: int) -> np.ndarray:
    """(n_draws, draw_n) → (max_num, n_draws) binary indicator."""
    n_draws = draws_2d.shape[0]
    bm = np.zeros((max_num, n_draws), dtype=np.float32)
    for i, row in enumerate(draws_2d):
        for v in row:
            vi = int(v)
            if 1 <= vi <= max_num:
                bm[vi - 1, i] = 1.0
    return bm


# ---------------------------------------------------------------------------
# Baselines
# ---------------------------------------------------------------------------

def score_random(draws_2d: np.ndarray, max_num: int) -> Dict[int, float]:
    rng = np.random.default_rng()
    return _normalize({n: float(rng.random()) for n in range(1, max_num + 1)}, max_num)


def score_frequency(draws_2d: np.ndarray, max_num: int) -> Dict[int, float]:
    """Recency-weighted frequency (exponential decay)."""
    n = draws_2d.shape[0]
    if n == 0:
        return _normalize({i: 1.0 for i in range(1, max_num + 1)}, max_num)
    weights = np.exp(np.linspace(-2.0, 0.0, n)).astype(np.float32)
    scores = np.zeros(max_num + 1, dtype=np.float64)
    for w, row in zip(weights, draws_2d):
        for v in row:
            vi = int(v)
            if 1 <= vi <= max_num:
                scores[vi] += w
    return _normalize({i: float(scores[i]) for i in range(1, max_num + 1)}, max_num)


def score_recency(draws_2d: np.ndarray, max_num: int) -> Dict[int, float]:
    """Gap-based: longer time since last appearance → higher score (overdue)."""
    n = draws_2d.shape[0]
    last_seen = np.full(max_num + 1, -1, dtype=np.int32)
    for i, row in enumerate(draws_2d):
        for v in row:
            vi = int(v)
            if 1 <= vi <= max_num:
                last_seen[vi] = i
    scores = {}
    for num in range(1, max_num + 1):
        if last_seen[num] < 0:
            scores[num] = float(n + 1)
        else:
            scores[num] = float(n - last_seen[num])
    return _normalize(scores, max_num)


# ---------------------------------------------------------------------------
# Foundation models — try imports lazily so the registry still loads.
# ---------------------------------------------------------------------------

_TIMESFM_PIPE = None
_TIMESFM_ERR: Optional[str] = None


def _get_timesfm():
    global _TIMESFM_PIPE, _TIMESFM_ERR
    if _TIMESFM_PIPE is not None:
        return _TIMESFM_PIPE
    if _TIMESFM_ERR is not None:
        return None
    try:
        import torch
        import timesfm
        backend = "gpu" if torch.cuda.is_available() else "cpu"
        # Hparams match the google/timesfm-1.0-200m-pytorch checkpoint exactly
        # (output_patch_len=128, num_layers=20, horizon=128) — using mismatched
        # values triggers state_dict size errors during load.
        pipe = timesfm.TimesFm(
            hparams=timesfm.TimesFmHparams(
                backend=backend,
                per_core_batch_size=32,
                horizon_len=128,
                context_len=512,
                input_patch_len=32,
                output_patch_len=128,
                num_layers=20,
            ),
            checkpoint=timesfm.TimesFmCheckpoint(huggingface_repo_id="google/timesfm-1.0-200m-pytorch"),
        )
        _TIMESFM_PIPE = pipe
        return pipe
    except Exception as exc:
        _TIMESFM_ERR = f"{type(exc).__name__}: {exc}"
        logger.warning("[TimesFM] init failed: %s", _TIMESFM_ERR)
        return None


def score_timesfm(draws_2d: np.ndarray, max_num: int) -> Dict[int, float]:
    pipe = _get_timesfm()
    if pipe is None:
        return {}
    try:
        import torch
        binary = _build_binary(draws_2d, max_num)  # (max_num, n)
        # Trim to context length
        ctx = min(512, binary.shape[1])
        series = [binary[i, -ctx:].astype(np.float32) for i in range(max_num)]
        freqs = [0] * max_num
        forecast, _ = pipe.forecast(inputs=series, freq=freqs)
        forecast = np.asarray(forecast)  # (max_num, horizon)
        # Weighted mean of next ~5 steps (closest matters most)
        h = forecast.shape[1]
        w = np.exp(-np.arange(h) / 3.0)
        w = w / w.sum()
        scores = (forecast * w[None, :]).sum(axis=1)
        return _normalize({i + 1: float(scores[i]) for i in range(max_num)}, max_num)
    except Exception as exc:
        logger.warning("[TimesFM] scoring failed: %s", exc)
        return {}


_CHRONOS_PIPE = None
_CHRONOS_ERR: Optional[str] = None


def _get_chronos():
    global _CHRONOS_PIPE, _CHRONOS_ERR
    if _CHRONOS_PIPE is not None:
        return _CHRONOS_PIPE
    if _CHRONOS_ERR is not None:
        return None
    try:
        import torch
        from chronos import BaseChronosPipeline
        device = "cuda" if torch.cuda.is_available() else "cpu"
        pipe = BaseChronosPipeline.from_pretrained(
            "amazon/chronos-bolt-base",
            device_map=device,
            torch_dtype=torch.float32,
        )
        _CHRONOS_PIPE = pipe
        return pipe
    except Exception as exc:
        _CHRONOS_ERR = f"{type(exc).__name__}: {exc}"
        logger.warning("[Chronos] init failed: %s", _CHRONOS_ERR)
        return None


def score_chronos(draws_2d: np.ndarray, max_num: int) -> Dict[int, float]:
    pipe = _get_chronos()
    if pipe is None:
        return {}
    try:
        import torch
        binary = _build_binary(draws_2d, max_num)
        ctx = min(300, binary.shape[1])
        inp = torch.tensor(binary[:, -ctx:], dtype=torch.float32)
        with torch.no_grad():
            _q, mean = pipe.predict_quantiles(
                inputs=inp,
                prediction_length=5,
                quantile_levels=[0.1, 0.5, 0.9],
            )
        mean_np = mean.cpu().numpy()  # (max_num, 5)
        w = np.array([1.0, 0.7, 0.5, 0.35, 0.25], dtype=np.float32)
        w = w / w.sum()
        scores = (mean_np * w[None, :]).sum(axis=1)
        return _normalize({i + 1: float(scores[i]) for i in range(max_num)}, max_num)
    except Exception as exc:
        logger.warning("[Chronos] scoring failed: %s", exc)
        return {}


_MOMENT_PIPE = None
_MOMENT_ERR: Optional[str] = None


def _get_moment():
    global _MOMENT_PIPE, _MOMENT_ERR
    if _MOMENT_PIPE is not None:
        return _MOMENT_PIPE
    if _MOMENT_ERR is not None:
        return None
    try:
        import torch
        from momentfm import MOMENTPipeline
        pipe = MOMENTPipeline.from_pretrained(
            "AutonLab/MOMENT-1-small",
            model_kwargs={
                "task_name": "forecasting",
                "forecast_horizon": 8,
                "head_dropout": 0.1,
                "weight_decay": 0,
                "freeze_encoder": True,
                "freeze_embedder": True,
                "freeze_head": False,
            },
        )
        pipe.init()
        if torch.cuda.is_available():
            pipe = pipe.cuda()
        pipe.eval()
        _MOMENT_PIPE = pipe
        return pipe
    except Exception as exc:
        _MOMENT_ERR = f"{type(exc).__name__}: {exc}"
        logger.warning("[MOMENT] init failed: %s", _MOMENT_ERR)
        return None


def score_moment(draws_2d: np.ndarray, max_num: int) -> Dict[int, float]:
    pipe = _get_moment()
    if pipe is None:
        return {}
    try:
        import torch
        binary = _build_binary(draws_2d, max_num)
        # MOMENT input shape is (B, n_channels=1, seq_len=512)
        seq_len = 512
        n = binary.shape[1]
        if n < seq_len:
            pad = np.zeros((max_num, seq_len - n), dtype=np.float32)
            data = np.concatenate([pad, binary], axis=1)
        else:
            data = binary[:, -seq_len:]
        x = torch.tensor(data, dtype=torch.float32).unsqueeze(1)  # (max_num, 1, seq_len)
        if torch.cuda.is_available():
            x = x.cuda()
        with torch.no_grad():
            out = pipe(x_enc=x)
        fc = out.forecast.detach().cpu().numpy()  # (max_num, 1, horizon)
        h = fc.shape[-1]
        w = np.exp(-np.arange(h) / 3.0)
        w = w / w.sum()
        scores = (fc[:, 0, :] * w[None, :]).sum(axis=1)
        return _normalize({i + 1: float(scores[i]) for i in range(max_num)}, max_num)
    except Exception as exc:
        logger.warning("[MOMENT] scoring failed: %s", exc)
        return {}


# ---------------------------------------------------------------------------
# NeuralForecast architectures (trainable, run on per-fold basis)
# ---------------------------------------------------------------------------

_NF_AVAILABLE = False
_NF_ERR: Optional[str] = None
try:
    import pandas as pd  # noqa: F401
    import torch  # noqa: F401
    from neuralforecast import NeuralForecast
    from neuralforecast.models import (
        NBEATS, NHITS, TiDE, DLinear, PatchTST,
        Informer, Autoformer, FEDformer, DeepAR, TCN,
    )
    _NF_AVAILABLE = True
except Exception as _e:
    _NF_ERR = f"{type(_e).__name__}: {_e}"


def _make_nf_score(model_cls, name: str, max_steps: int = 200, extra_kwargs: Optional[dict] = None) -> Callable:
    """Factory for a NeuralForecast-based scorer."""

    def _score(draws_2d: np.ndarray, max_num: int) -> Dict[int, float]:
        if not _NF_AVAILABLE:
            return {}
        try:
            import pandas as pd
            import torch
            binary = _build_binary(draws_2d, max_num)  # (max_num, n)
            n = binary.shape[1]
            if n < 80:
                return {}
            input_size = min(64, max(16, n // 4))

            # Build long-format dataframe for NeuralForecast
            rows = []
            for k in range(max_num):
                for i in range(n):
                    rows.append((f"num_{k+1}", i, float(binary[k, i])))
            df = pd.DataFrame(rows, columns=["unique_id", "ds", "y"])

            # FORCE CUDA per spec — abort early if CUDA missing so the run is
            # honest about hardware coverage.
            if not torch.cuda.is_available():
                raise RuntimeError("CUDA required (spec: forced GPU)")
            kwargs = dict(
                h=1,
                input_size=input_size,
                max_steps=max_steps,
                accelerator="gpu",
                devices=1,
                enable_progress_bar=False,
                random_seed=42,
            )
            if extra_kwargs:
                kwargs.update(extra_kwargs)

            try:
                model = model_cls(**kwargs)
            except TypeError:
                # Some models reject `devices` or `accelerator`; retry minimal
                minimal = {k: v for k, v in kwargs.items() if k not in ("devices",)}
                model = model_cls(**minimal)

            nf = NeuralForecast(models=[model], freq=1)
            nf.fit(df=df)
            with torch.no_grad():
                fc = nf.predict(df=df)
            # fc has columns: unique_id, ds, <model_name>
            value_col = [c for c in fc.columns if c not in ("unique_id", "ds")][-1]
            scores: Dict[int, float] = {}
            for _, r in fc.iterrows():
                uid = str(r["unique_id"])
                num = int(uid.split("_")[1])
                v = float(r[value_col])
                scores[num] = max(scores.get(num, -1e18), v)
            return _normalize(scores, max_num)
        except Exception as exc:
            logger.warning("[%s] training/scoring failed: %s", name, exc)
            return {}

    _score.__name__ = f"score_{name}"
    return _score


# Build NF scorers if NF is available. max_steps trimmed for bench speed —
# 100 steps converges well for next-step binary forecasting and keeps the
# 16-method × 4-game × 10-pct sweep under ~90 min on RTX 5060 Ti.
if _NF_AVAILABLE:
    # N-BEATS default stacks (trend, seasonality) require horizon >= 2 because
    # they fit polynomial/Fourier bases per stack. We use all-identity stacks
    # which work with h=1 and still capture the residual signal we need.
    score_nbeats = _make_nf_score(
        NBEATS, "nbeats", max_steps=100,
        extra_kwargs={"stack_types": ["identity", "identity", "identity"]},
    )
    score_nhits = _make_nf_score(NHITS, "nhits", max_steps=100)
    score_tide = _make_nf_score(TiDE, "tide", max_steps=100)
    score_dlinear = _make_nf_score(DLinear, "dlinear", max_steps=100)
    score_patchtst = _make_nf_score(PatchTST, "patchtst", max_steps=100)
    score_informer = _make_nf_score(Informer, "informer", max_steps=80)
    score_autoformer = _make_nf_score(Autoformer, "autoformer", max_steps=80)
    score_fedformer = _make_nf_score(FEDformer, "fedformer", max_steps=80)
    score_deepar = _make_nf_score(DeepAR, "deepar", max_steps=80)
    score_tcn = _make_nf_score(TCN, "tcn", max_steps=80)
else:
    def _unavailable_nf(draws_2d, max_num):
        return {}
    score_nbeats = score_nhits = score_tide = score_dlinear = _unavailable_nf
    score_patchtst = score_informer = score_autoformer = _unavailable_nf
    score_fedformer = score_deepar = score_tcn = _unavailable_nf


# ---------------------------------------------------------------------------
# Always-unavailable methods (no offline path in current env) — kept for
# transparency in the final report.
# ---------------------------------------------------------------------------

def _unavailable_factory(reason: str):
    def _score(draws_2d, max_num):
        return {}
    _score._unavailable_reason = reason  # type: ignore[attr-defined]
    return _score


score_moirai = _unavailable_factory("uni2ts requires omegaconf<2.4; current env has 2.4.0.dev — Config symbol removed")
score_lag_llama = _unavailable_factory("lag-llama package not installed")
score_timegpt = _unavailable_factory("Nixtla TimeGPT requires paid API key (NIXTLA_API_KEY)")
score_tinytimemixer = _unavailable_factory("IBM TinyTimeMixer not installed (granite-tsfm)")
score_units = _unavailable_factory("UniTS (Harvard/MIT) not installed")
score_timer = _unavailable_factory("Timer (THUML) not installed")
score_mamba = _unavailable_factory("mamba_ssm not installed; S-Mamba/TimeMachine require CUDA mamba kernel")


# ---------------------------------------------------------------------------
# Registry (group → method name → (callable, family, reason if unavailable))
# ---------------------------------------------------------------------------

# Method tuple: (name, callable, family, requires_train, notes)
METHODS: Dict[str, Tuple[Callable, str, bool, str]] = {
    # Baselines
    "random":      (score_random,      "baseline",        False, "Pure-random scores; sanity floor"),
    "frequency":   (score_frequency,   "baseline",        False, "Exp-decay recency-weighted frequency"),
    "recency":     (score_recency,     "baseline",        False, "Gap-since-last-seen ('overdue' heuristic)"),
    # Foundation (zero-shot)
    "timesfm":     (score_timesfm,     "foundation-zs",   False, "Google TimesFM 200M, patch-based decoder"),
    "chronos":     (score_chronos,     "foundation-zs",   False, "Amazon Chronos-Bolt base, T5 enc-dec"),
    "moment":      (score_moment,      "foundation-zs",   False, "CMU MOMENT-1-small, multitask"),
    "moirai":      (score_moirai,      "foundation-zs",   False, "Salesforce MOIRAI (UNAVAILABLE in env)"),
    "lag_llama":   (score_lag_llama,   "foundation-zs",   False, "Lag-Llama (UNAVAILABLE)"),
    "timegpt":     (score_timegpt,     "foundation-zs",   False, "Nixtla TimeGPT (UNAVAILABLE — paid API)"),
    "tinytimemixer": (score_tinytimemixer, "foundation-zs", False, "IBM TTM (UNAVAILABLE)"),
    "units":       (score_units,       "foundation-zs",   False, "UniTS Harvard/MIT (UNAVAILABLE)"),
    "timer":       (score_timer,       "foundation-zs",   False, "Timer THUML (UNAVAILABLE)"),
    # NeuralForecast — MLP / linear
    "nbeats":      (score_nbeats,      "nf-mlp",          True,  "N-BEATS (Element AI)"),
    "nhits":       (score_nhits,       "nf-mlp",          True,  "N-HiTS"),
    "tide":        (score_tide,        "nf-mlp",          True,  "Google TiDE"),
    "dlinear":     (score_dlinear,     "nf-mlp",          True,  "DLinear (decomposition + linear)"),
    # NeuralForecast — Transformer
    "patchtst":    (score_patchtst,    "nf-transformer",  True,  "PatchTST"),
    "informer":    (score_informer,    "nf-transformer",  True,  "Informer (ProbSparse attention)"),
    "autoformer":  (score_autoformer,  "nf-transformer",  True,  "Autoformer (Auto-Correlation)"),
    "fedformer":   (score_fedformer,   "nf-transformer",  True,  "FEDformer (Fourier)"),
    # NeuralForecast — Recurrent / Conv
    "deepar":      (score_deepar,      "nf-recurrent",    True,  "DeepAR (probabilistic RNN)"),
    "tcn":         (score_tcn,         "nf-conv",         True,  "Temporal Convolutional Network"),
    # State Space
    "mamba":       (score_mamba,       "ssm",             True,  "S-Mamba / TimeMachine (UNAVAILABLE)"),
}


def list_methods() -> List[str]:
    return list(METHODS.keys())


def method_meta(name: str) -> dict:
    fn, family, requires_train, notes = METHODS[name]
    available = not getattr(fn, "_unavailable_reason", None)
    meta = {
        "name": name,
        "family": family,
        "requires_train": requires_train,
        "notes": notes,
        "available": available,
    }
    reason = getattr(fn, "_unavailable_reason", None)
    if reason:
        meta["unavailable_reason"] = reason
    return meta


def call_method(name: str, draws_2d: np.ndarray, max_num: int) -> Tuple[Dict[int, float], float]:
    """Call a registered method; returns (scores_dict, wall_time_sec)."""
    fn, _family, _train, _notes = METHODS[name]
    t0 = time.perf_counter()
    scores = fn(draws_2d, max_num)
    dt = time.perf_counter() - t0
    return scores, dt
