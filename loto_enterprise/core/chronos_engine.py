"""Chronos-Bolt scoring (ensemble complementar pentru TimesFM).

Folosește amazon/chronos-bolt-base (205M params, T5-based) ca al doilea
foundation model. Chronos rulează ~50× mai rapid decât TimesFM (250ms per
batch de 45 serii vs 12-18s pentru TimesFM), deci poate fi adăugat la
pipeline cu cost neglijabil.

Scopul: ensemble — combinăm predicțiile celor două foundation models
diferite pentru a captura semnal complementar (TimesFM are arhitectură
decoder-only, Chronos T5-encoder-decoder).
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

import numpy as np

try:
    import torch
    from chronos import BaseChronosPipeline
    HAS_CHRONOS = True
except ImportError as e:
    HAS_CHRONOS = False
    _CHRONOS_ERROR = str(e)
    logging.warning("[CHRONOS] Library not available: %s", e)


# Singleton pentru a păstra modelul în memorie
_GLOBAL_CHRONOS_PIPE = None
_CHRONOS_MODEL_NAME = "amazon/chronos-bolt-base"


def _get_chronos_pipeline():
    """Încărcare lazy + cache pentru pipeline-ul Chronos."""
    global _GLOBAL_CHRONOS_PIPE
    if not HAS_CHRONOS:
        return None
    if _GLOBAL_CHRONOS_PIPE is not None:
        return _GLOBAL_CHRONOS_PIPE

    device = "cuda" if torch.cuda.is_available() else "cpu"
    logging.info("[CHRONOS] Loading %s on %s (cold start)...", _CHRONOS_MODEL_NAME, device)
    try:
        pipe = BaseChronosPipeline.from_pretrained(
            _CHRONOS_MODEL_NAME,
            device_map=device,
            torch_dtype=torch.float32,
        )
        _GLOBAL_CHRONOS_PIPE = pipe
        logging.info("[CHRONOS] Loaded successfully (205M params).")
        return pipe
    except Exception as exc:
        logging.error("[CHRONOS] Failed to load: %s", exc)
        return None


def get_chronos_scores(
    draw_matrix: np.ndarray | None,
    joker_vals: np.ndarray | None,
    max_num: int,
    is_joker_drum: bool = False,
    context_window: int = 300,
    prediction_length: int = 5,
) -> Dict[int, float]:
    """Calculează scoruri Chronos pentru fiecare număr 1..max_num.

    Pentru fiecare număr construim seria binară (1 dacă numărul a apărut, 0 altfel)
    și o trimitem prin Chronos batch. Scor = media predicțiilor pe primele
    prediction_length pași (orizont scurt = mai precis pentru next-draw).

    Returns: {num: score} normalizat în [0, 1].
    """
    pipe = _get_chronos_pipeline()
    if pipe is None:
        return {}

    # Construim seriile binare (vectorizat, ca în build_binary_series TimesFM).
    if is_joker_drum and joker_vals is not None:
        window_data = np.asarray(joker_vals[-context_window:], dtype=np.int64)
        n_steps = window_data.shape[0]
        if n_steps < 32:
            return {}
        nums = np.arange(1, max_num + 1, dtype=np.int64)
        # Broadcast: (max_num, n_steps)
        binary = (window_data[None, :] == nums[:, None]).astype(np.float32)
    elif draw_matrix is not None:
        window_data = np.asarray(draw_matrix[-context_window:], dtype=np.int64)
        n_steps = window_data.shape[0]
        if n_steps < 32:
            return {}
        nums = np.arange(1, max_num + 1, dtype=np.int64)
        # Broadcast: (max_num, n_steps, draw_n) → reduce → (max_num, n_steps)
        binary = (window_data[None, :, :] == nums[:, None, None]).any(axis=2).astype(np.float32)
    else:
        return {}

    # Tensor + GPU transfer
    inputs = torch.tensor(binary, dtype=torch.float32)

    try:
        # Chronos batch forecast — toate seriile într-un singur apel
        with torch.no_grad():
            quantiles, mean = pipe.predict_quantiles(
                inputs=inputs,
                prediction_length=prediction_length,
                quantile_levels=[0.1, 0.5, 0.9],
            )
        # mean shape: (max_num, prediction_length)
        # Scor = media pe primele predict_length pași (probabilitate fired)
        mean_np = mean.cpu().numpy()
        # Pentru next-draw mai contează primii 1-2 pași; weighted avg cu decay.
        weights = np.array([1.0, 0.7, 0.5, 0.35, 0.25][:prediction_length], dtype=np.float32)
        weights = weights / weights.sum()
        # Weighted mean per series
        scores_arr = (mean_np[:, :prediction_length] * weights[None, :]).sum(axis=1)

        # Normalizare MinMax în [0, 1] pentru a fi compatibil cu NQI
        s_min, s_max = float(scores_arr.min()), float(scores_arr.max())
        rng = max(s_max - s_min, 1e-6)
        scores_norm = (scores_arr - s_min) / rng

        result = {int(n): float(scores_norm[i]) for i, n in enumerate(nums)}
        return result
    except Exception as exc:
        logging.error("[CHRONOS] Forecast failed: %s", exc)
        return {}


def blend_with_timesfm(
    timesfm_scores: Dict[int, float],
    chronos_scores: Dict[int, float],
    alpha: float = 0.7,
) -> Dict[int, float]:
    """Blend liniar TimesFM + Chronos. alpha = ponderea TimesFM (0.7 default).

    Ambele scoruri normalizate în [0, 1] înainte de combinare. Pentru numerele
    care lipsesc dintr-unul, folosim doar celălalt (fallback robust).
    """
    if not chronos_scores:
        return timesfm_scores
    if not timesfm_scores:
        return chronos_scores

    # Normalizare TimesFM (poate avea scoruri în orice scară)
    t_vals = list(timesfm_scores.values())
    t_min, t_max = min(t_vals), max(t_vals)
    t_rng = max(t_max - t_min, 1e-6)
    t_norm = {k: (v - t_min) / t_rng for k, v in timesfm_scores.items()}

    all_nums = set(timesfm_scores.keys()) | set(chronos_scores.keys())
    blended: Dict[int, float] = {}
    for n in all_nums:
        t = t_norm.get(n)
        c = chronos_scores.get(n)
        if t is not None and c is not None:
            blended[n] = alpha * t + (1.0 - alpha) * c
        elif t is not None:
            blended[n] = t
        elif c is not None:
            blended[n] = c
    return blended
