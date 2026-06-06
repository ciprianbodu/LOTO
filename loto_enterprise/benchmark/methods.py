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

# Tensor Cores (RTX): folosim unitățile specializate de matmul/conv ale GPU-ului dacă
# le DETECTĂM și sunt UTILE — antrenare ~1.5-3× mai rapidă a rețelelor din bench
# (NeuralForecast/torch), cu pierdere de precizie nesemnificativă (irelevant pe loto).
# Se aplică în ORICE proces care importă methods (inclusiv worker-ii GPU ai bench-ului).
# Dezactivabil cu LOTO_TF32=0. Rulează o singură dată la import.
try:
    import os as _os_init
    import torch as _torch_init
    if _os_init.environ.get("LOTO_TF32", "1") != "0" and _torch_init.cuda.is_available():
        # capability >= (8,0) = Ampere+ (RTX 30xx/40xx, A100) → TF32 pe Tensor Cores.
        # (7,x) = Volta/Turing: au Tensor Cores FP16, dar nu TF32; flag-urile sunt no-op
        # inofensiv acolo. cudnn.benchmark autotune-uiește kernel-urile conv (repetate).
        try:
            _cap = _torch_init.cuda.get_device_capability(0)
        except Exception:  # noqa: BLE001
            _cap = (0, 0)
        _torch_init.backends.cudnn.benchmark = True
        _torch_init.backends.cuda.matmul.allow_tf32 = True       # TF32 pt matmul (Tensor Cores)
        _torch_init.backends.cudnn.allow_tf32 = True             # TF32 pt convoluții cuDNN
        if hasattr(_torch_init, "set_float32_matmul_precision"):
            _torch_init.set_float32_matmul_precision("high")     # = TF32
        _name = _torch_init.cuda.get_device_name(0)
        _tc = "TF32 (Ampere+)" if _cap >= (8, 0) else ("FP16-only (Volta/Turing)" if _cap >= (7, 0)
                                                       else "fără Tensor Cores")
        logger.info("[methods] Tensor Cores: %s pe %s (cc=%s) — cudnn.benchmark+TF32 activate",
                    _tc, _name, _cap)
except Exception:  # noqa: BLE001
    pass


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
        # Existing (10 models)
        NBEATS, NHITS, TiDE, DLinear, PatchTST,
        Informer, Autoformer, FEDformer, DeepAR, TCN,
        # NEW DL univariate (12 models, h=1 compatible with defaults)
        BiTCN, DeepNPTS, DilatedRNN, GRU, KAN, LSTM,
        MLP, NLinear, RNN, TFT, TimesNet, VanillaTransformer,
        # NEW DL multivariate (6 models, require n_series=max_num)
        RMoK, SOFTS, TSMixer, TimeMixer, TimeXer, iTransformer,
        # NEW DL with special config (1 model — needs identity stacks for h=1)
        NBEATSx,
    )
    _NF_AVAILABLE = True
except Exception as _e:
    _NF_ERR = f"{type(_e).__name__}: {_e}"


def _make_nf_score(
    model_cls,
    name: str,
    max_steps: int = 200,
    extra_kwargs: Optional[dict] = None,
    multivariate: bool = False,
) -> Callable:
    """Factory for a NeuralForecast-based scorer.

    multivariate=True: adds n_series=max_num at call-time (modelele
    multivariate procesează toate seriile simultan cu corelații cross-series:
    RMoK, SOFTS, TSMixer, TimeMixer, TimeXer, iTransformer).
    """

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
            if multivariate:
                # n_series e numărul de serii corelate (max_num pentru loto)
                kwargs["n_series"] = max_num
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
    # === EXISTING (10 models, calibrated) ===
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

    # === NEW DL — Univariate (12 models, h=1 compatible) ===
    # max_steps mai mici pentru modelele recurente (RNN/LSTM/GRU/DilatedRNN)
    # ca să nu explodăm timpul de bench. Pentru transformere și mixere folosim
    # 80 (echivalent cu existing transformer-ele).
    score_bitcn = _make_nf_score(BiTCN, "bitcn", max_steps=80)
    score_deepnpts = _make_nf_score(DeepNPTS, "deepnpts", max_steps=80)
    score_dilatedrnn = _make_nf_score(DilatedRNN, "dilatedrnn", max_steps=60)
    score_gru = _make_nf_score(GRU, "gru", max_steps=60)
    score_kan = _make_nf_score(KAN, "kan", max_steps=100)
    score_lstm = _make_nf_score(LSTM, "lstm", max_steps=60)
    score_mlp = _make_nf_score(MLP, "mlp", max_steps=100)
    score_nlinear = _make_nf_score(NLinear, "nlinear", max_steps=100)
    score_rnn = _make_nf_score(RNN, "rnn", max_steps=60)
    score_tft = _make_nf_score(TFT, "tft", max_steps=80)
    score_timesnet = _make_nf_score(TimesNet, "timesnet", max_steps=80)
    score_vanilla_transformer = _make_nf_score(VanillaTransformer, "vanilla_transformer", max_steps=80)

    # === NEW DL — Multivariate (6 models, need n_series) ===
    score_rmok = _make_nf_score(RMoK, "rmok", max_steps=80, multivariate=True)
    score_softs = _make_nf_score(SOFTS, "softs", max_steps=80, multivariate=True)
    score_tsmixer = _make_nf_score(TSMixer, "tsmixer", max_steps=100, multivariate=True)
    score_timemixer = _make_nf_score(TimeMixer, "timemixer", max_steps=80, multivariate=True)
    score_timexer = _make_nf_score(TimeXer, "timexer", max_steps=80, multivariate=True)
    score_itransformer = _make_nf_score(iTransformer, "itransformer", max_steps=80, multivariate=True)

    # === NEW DL — Special config (1 model) ===
    # NBEATSx same constraint as NBEATS: identity stacks for h=1
    score_nbeatsx = _make_nf_score(
        NBEATSx, "nbeatsx", max_steps=100,
        extra_kwargs={"stack_types": ["identity", "identity"]},
    )
else:
    def _unavailable_nf(draws_2d, max_num):
        return {}
    # Existing
    score_nbeats = score_nhits = score_tide = score_dlinear = _unavailable_nf
    score_patchtst = score_informer = score_autoformer = _unavailable_nf
    score_fedformer = score_deepar = score_tcn = _unavailable_nf
    # New DL univariate
    score_bitcn = score_deepnpts = score_dilatedrnn = score_gru = _unavailable_nf
    score_kan = score_lstm = score_mlp = score_nlinear = _unavailable_nf
    score_rnn = score_tft = score_timesnet = score_vanilla_transformer = _unavailable_nf
    # New DL multivariate
    score_rmok = score_softs = score_tsmixer = score_timemixer = _unavailable_nf
    score_timexer = score_itransformer = _unavailable_nf
    # Special config
    score_nbeatsx = _unavailable_nf


# ---------------------------------------------------------------------------
# Always-unavailable methods (no offline path in current env) — kept for
# transparency in the final report.
# ---------------------------------------------------------------------------

def _unavailable_factory(reason: str):
    def _score(draws_2d, max_num):
        return {}
    _score._unavailable_reason = reason  # type: ignore[attr-defined]
    return _score


# ---------------------------------------------------------------------------
# ENSEMBLE METHODS — combine top-K methods via averaged normalized scores
# Discovered empirically (scratch/mini_bench_4plus.py, 2026-05-25):
#   frequency, autoformer, kan = best 4+ hits on 30 walk-forward draws.
#   Ensemble averages them, hoping borrowed strengths offset individual weaknesses.
# ---------------------------------------------------------------------------

def _make_ensemble(method_names: List[str], name: str) -> Callable:
    """Build an ensemble scorer that averages normalized scores of N methods."""
    def _ensemble_score(draws_2d: np.ndarray, max_num: int) -> Dict[int, float]:
        all_scores: List[Dict[int, float]] = []
        for m in method_names:
            try:
                if m not in METHODS:
                    continue
                fn, _f, _t, _n = METHODS[m]
                if getattr(fn, "_unavailable_reason", None):
                    continue
                sc = fn(draws_2d, max_num)
                if sc:
                    # Re-normalize each member to [0, 1] before averaging
                    all_scores.append(_normalize(sc, max_num))
            except Exception as exc:
                logger.warning("[ENSEMBLE %s] member %s failed: %s", name, m, exc)
        if not all_scores:
            return {}
        # Average
        combined: Dict[int, float] = {n: 0.0 for n in range(1, max_num + 1)}
        for sc in all_scores:
            for n, v in sc.items():
                combined[n] = combined.get(n, 0.0) + v
        for n in combined:
            combined[n] /= len(all_scores)
        return _normalize(combined, max_num)

    _ensemble_score.__name__ = f"score_{name}"
    return _ensemble_score


# Top-5 empiric (frequency + autoformer + kan + tide + rnn) — wider coverage
score_ensemble_top5 = _make_ensemble(
    ["frequency", "autoformer", "kan", "tide", "rnn"],
    "ensemble_top5",
)

# Diversified: 3 families (baseline + transformer + recurrent)
score_ensemble_diverse = _make_ensemble(
    ["frequency", "fedformer", "deepar"],
    "ensemble_diverse",
)


score_moirai = _unavailable_factory("uni2ts requires omegaconf<2.4; current env has 2.4.0.dev — Config symbol removed")
score_lag_llama = _unavailable_factory("lag-llama package not installed")
score_timegpt = _unavailable_factory("Nixtla TimeGPT requires paid API key (NIXTLA_API_KEY)")
score_tinytimemixer = _unavailable_factory("IBM TinyTimeMixer not installed (granite-tsfm)")
score_units = _unavailable_factory("UniTS (Harvard/MIT) not installed")
score_timer = _unavailable_factory("Timer (THUML) not installed")
score_mamba = _unavailable_factory("mamba_ssm not installed; S-Mamba/TimeMachine require CUDA mamba kernel")
score_xlstm = _unavailable_factory("xLSTM package not installed (`pip install xlstm mlstm_kernels`)")
score_time_llm = _unavailable_factory("TimeLLM requires LLM backbone (llama/qwen) — heavy dependency, deferred")


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
    # === NEW DL (univariate, 12 models) ===
    # Convolutional / Recurrent / Mixer DL family
    "bitcn":       (score_bitcn,       "nf-conv",         True,  "Bidirectional TCN (forward+backward causality)"),
    "deepnpts":    (score_deepnpts,    "nf-recurrent",    True,  "Deep Non-Parametric Time Series"),
    "dilatedrnn":  (score_dilatedrnn,  "nf-recurrent",    True,  "Dilated RNN (multi-scale temporal)"),
    "gru":         (score_gru,         "nf-recurrent",    True,  "Gated Recurrent Unit (lighter LSTM)"),
    "lstm":        (score_lstm,        "nf-recurrent",    True,  "Long Short-Term Memory"),
    "rnn":         (score_rnn,         "nf-recurrent",    True,  "Vanilla RNN (baseline DL)"),
    # MLP / linear DL family
    "mlp":         (score_mlp,         "nf-mlp",          True,  "Simple MLP baseline DL"),
    "nlinear":     (score_nlinear,     "nf-mlp",          True,  "NLinear (normalized DLinear variant)"),
    "nbeatsx":     (score_nbeatsx,     "nf-mlp",          True,  "N-BEATSx (extended N-BEATS w/ exog)"),
    "kan":         (score_kan,         "nf-mlp",          True,  "Kolmogorov-Arnold Network (NEW 2024)"),
    # Transformer DL family
    "tft":         (score_tft,         "nf-transformer",  True,  "Temporal Fusion Transformer (Google, industry std)"),
    "vanilla_transformer": (score_vanilla_transformer, "nf-transformer", True, "Vanilla Transformer (baseline DL)"),
    "timesnet":    (score_timesnet,    "nf-conv",         True,  "TimesNet (multi-scale TCN, SoTA 2023)"),
    # === NEW DL (multivariate, 6 models — n_series cross-correlation) ===
    "rmok":        (score_rmok,        "nf-mlp-multi",    True,  "Recurrent Mixture of KAN (multivariate)"),
    "softs":       (score_softs,       "nf-mlp-multi",    True,  "Series-cOre Fused Time Series (multivariate)"),
    "tsmixer":     (score_tsmixer,     "nf-mlp-multi",    True,  "Google TSMixer (MLP-Mixer for TS, multivariate)"),
    "timemixer":   (score_timemixer,   "nf-mlp-multi",    True,  "TimeMixer (multivariate decomposition)"),
    "timexer":     (score_timexer,     "nf-transformer-multi", True, "TimeXer (cross-attention multivariate)"),
    "itransformer": (score_itransformer, "nf-transformer-multi", True, "iTransformer (Inverted Transformer 2024)"),
    # State Space (unavailable)
    "mamba":       (score_mamba,       "ssm",             True,  "S-Mamba / TimeMachine (UNAVAILABLE)"),
    "xlstm":       (score_xlstm,       "ssm",             True,  "xLSTM Extended LSTM 2024 (UNAVAILABLE — needs pip xlstm)"),
    # LLM-based (unavailable)
    "time_llm":    (score_time_llm,    "llm-ts",          True,  "TimeLLM (UNAVAILABLE — needs LLM backbone)"),
    # === ENSEMBLE — combine top-K methods, discovered empirically 2026-05-25 ===
    "ensemble_top5":    (score_ensemble_top5,    "ensemble", False, "Average of top-5 (frequency+autoformer+kan+tide+rnn)"),
    "ensemble_diverse": (score_ensemble_diverse, "ensemble", False, "Diversified ensemble (frequency + fedformer + deepar)"),
}


# ============================================================================
# EXTENSIONS — extra ~50 methods loaded from methods_classical.py,
# methods_ml.py, methods_torch_extra.py. Loaded lazily; if any module is
# missing or import fails, the loader logs and continues with what is available.
# ============================================================================
def _load_extra_methods() -> None:
    """Merge METHODS dicts from extension modules into the global METHODS."""
    global METHODS
    extensions = []
    try:
        from . import methods_classical
        extensions.append(("methods_classical", methods_classical.CLASSICAL_METHODS))
    except Exception as exc:
        logger.debug(f"[methods] methods_classical not loaded: {exc}")
    try:
        from . import methods_ml
        extensions.append(("methods_ml", methods_ml.ML_METHODS))
    except Exception as exc:
        logger.debug(f"[methods] methods_ml not loaded: {exc}")
    try:
        from . import methods_torch_extra
        extensions.append(("methods_torch_extra", methods_torch_extra.TORCH_EXTRA_METHODS))
    except Exception as exc:
        logger.debug(f"[methods] methods_torch_extra not loaded: {exc}")
    try:
        from . import methods_torch_advanced
        extensions.append(("methods_torch_advanced", methods_torch_advanced.TORCH_ADVANCED_METHODS))
    except Exception as exc:
        logger.debug(f"[methods] methods_torch_advanced not loaded: {exc}")
    try:
        from . import methods_geometry
        extensions.append(("methods_geometry", methods_geometry.GEOMETRY_METHODS))
    except Exception as exc:
        logger.debug(f"[methods] methods_geometry not loaded: {exc}")
    try:
        from . import methods_coverage
        extensions.append(("methods_coverage", methods_coverage.COVERAGE_METHODS))
    except Exception as exc:
        logger.debug(f"[methods] methods_coverage not loaded: {exc}")
    try:
        from . import methods_omnius
        extensions.append(("methods_omnius", methods_omnius.OMNIUS_METHODS))
    except Exception as exc:
        logger.debug(f"[methods] methods_omnius not loaded: {exc}")

    added = 0
    for modname, extra_dict in extensions:
        for name, tup in extra_dict.items():
            if name not in METHODS:
                METHODS[name] = tup
                added += 1
    if added > 0:
        logger.info(f"[methods] Loaded {added} extra prediction methods from extensions ({len(extensions)} modules).")


# Load extensions at module import time.
try:
    _load_extra_methods()
except Exception as _ext_exc:
    logger.warning(f"[methods] Extra methods load failed: {_ext_exc}")


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
