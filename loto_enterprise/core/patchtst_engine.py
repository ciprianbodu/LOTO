"""PatchTST scoring (ensemble complementar pentru TimesFM, gated pe 6/49).

Folosește NeuralForecast PatchTST (Transformer-based time series model).
Trade-off vs TimesFM observat empiric pe 6/49:
  - PatchTST: +11% avg union hits, dar 0% 4+ events
  - TimesFM: -11% avg dar 2.63% 4+ events
Hybrid blend ar putea capta ambele beneficii.

Spre deosebire de TimesFM/Chronos (foundation models pre-trained),
PatchTST se antrenează LOCAL pe binary series. Train cost: ~10s per joc.
Cache-uim modelul în-process să rulăm inference fast în walk-forward.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

try:
    import torch
    from neuralforecast import NeuralForecast
    from neuralforecast.models import PatchTST
    HAS_PATCHTST = True
except ImportError as e:
    HAS_PATCHTST = False
    _PATCHTST_ERROR = str(e)
    logging.warning("[PatchTST] Library not available: %s", e)


# Cache global per-(max_num, train_data_hash)
_GLOBAL_PATCHTST_CACHE: Dict[str, object] = {}


def _train_key(max_num: int, n_train: int, last_draw_hash: int) -> str:
    return f"max{max_num}_n{n_train}_h{last_draw_hash}"


def _build_train_df(binary_matrix: np.ndarray, max_num: int) -> pd.DataFrame:
    """(n_draws, max_num) → DataFrame Nixtla format."""
    n_draws = binary_matrix.shape[0]
    rows = []
    for k in range(max_num):
        for i in range(n_draws):
            rows.append({
                "unique_id": f"num_{k+1}",
                "ds": i,
                "y": float(binary_matrix[i, k]),
            })
    return pd.DataFrame(rows)


def _build_binary_matrix(draw_matrix: np.ndarray, max_num: int) -> np.ndarray:
    """draw_matrix (n, draw_n) → binary (n, max_num)."""
    n_draws = draw_matrix.shape[0]
    bm = np.zeros((n_draws, max_num), dtype=np.float32)
    for i, row in enumerate(draw_matrix):
        for v in row:
            vi = int(v)
            if 1 <= vi <= max_num:
                bm[i, vi - 1] = 1.0
    return bm


def _train_or_load_patchtst(binary_matrix: np.ndarray, max_num: int) -> object:
    """Train PatchTST (sau întoarce cache) pe binary_matrix."""
    if not HAS_PATCHTST:
        return None

    n_train = binary_matrix.shape[0]
    # Hash bazat pe ultimele 10 rânduri ca să detectăm draw_matrix nou
    last_hash = int(np.sum(binary_matrix[-10:] * np.arange(1, 11)[:, None])) if n_train >= 10 else 0
    key = _train_key(max_num, n_train, last_hash)
    if key in _GLOBAL_PATCHTST_CACHE:
        return _GLOBAL_PATCHTST_CACHE[key]

    df = _build_train_df(binary_matrix, max_num)
    logging.info("[PatchTST] Training (key=%s, %d series)...", key, max_num)

    model = PatchTST(
        h=1,
        input_size=64,
        max_steps=300,
        accelerator="gpu" if torch.cuda.is_available() else "cpu",
        devices=1,
        enable_progress_bar=False,
        random_seed=42,
    )
    nf = NeuralForecast(models=[model], freq=1)
    nf.fit(df=df)

    _GLOBAL_PATCHTST_CACHE[key] = nf
    # Mențin cache mic
    if len(_GLOBAL_PATCHTST_CACHE) > 5:
        _GLOBAL_PATCHTST_CACHE.pop(next(iter(_GLOBAL_PATCHTST_CACHE)))
    return nf


def get_patchtst_scores(
    draw_matrix: np.ndarray | None,
    max_num: int,
    pool_size_hint: int = 10,
) -> Dict[int, float]:
    """Calculează scoruri PatchTST pentru fiecare număr 1..max_num.

    Returns: {num: score} normalizat în [0, 1].
    """
    if not HAS_PATCHTST or draw_matrix is None or draw_matrix.shape[0] < 100:
        return {}

    try:
        binary_matrix = _build_binary_matrix(draw_matrix, max_num)
        nf = _train_or_load_patchtst(binary_matrix, max_num)
        if nf is None:
            return {}

        # Predict next step folosind history complet
        df_history = _build_train_df(binary_matrix, max_num)
        with torch.no_grad():
            forecasts = nf.predict(df=df_history)

        scores: Dict[int, float] = {}
        for _, row in forecasts.iterrows():
            uid = row["unique_id"]
            num = int(uid.split("_")[1])
            pred_val = float(row.get("PatchTST", 0.0))
            scores[num] = max(scores.get(num, -1e9), pred_val)

        # Normalize MinMax to [0, 1]
        if not scores:
            return {}
        s_min, s_max = min(scores.values()), max(scores.values())
        rng = max(s_max - s_min, 1e-6)
        return {k: (v - s_min) / rng for k, v in scores.items()}
    except Exception as exc:
        logging.error("[PatchTST] scoring failed: %s", exc)
        return {}


def blend_with_timesfm(
    timesfm_scores: Dict[int, float],
    patchtst_scores: Dict[int, float],
    alpha: float = 0.7,
) -> Dict[int, float]:
    """Blend liniar TimesFM + PatchTST. alpha = pondere TimesFM."""
    if not patchtst_scores:
        return timesfm_scores
    if not timesfm_scores:
        return patchtst_scores

    t_vals = list(timesfm_scores.values())
    t_min, t_max = min(t_vals), max(t_vals)
    t_rng = max(t_max - t_min, 1e-6)
    t_norm = {k: (v - t_min) / t_rng for k, v in timesfm_scores.items()}

    all_nums = set(timesfm_scores.keys()) | set(patchtst_scores.keys())
    out: Dict[int, float] = {}
    for n in all_nums:
        t = t_norm.get(n)
        p = patchtst_scores.get(n)
        if t is not None and p is not None:
            out[n] = alpha * t + (1.0 - alpha) * p
        elif t is not None:
            out[n] = t
        else:
            out[n] = p
    return out
