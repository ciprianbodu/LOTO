"""Metode de scoring bazate pe COVER DESIGN — acoperire combinatorică.

Filosofie DISTINCTĂ față de frecvență: nu contează doar cât de des apare un
număr, ci cât de mult ACOPERĂ extrageri care nu sunt deja acoperite.

Onestitate: pe loterie aleatoare nu prezic — concurează în benchmark; dacă nu
bat random la 4+, decizia nu le alege.

Metodele cover_* blacklistate (greedy, rarity, winslips, etc.) au fost
ELIMINATE din cod — rămâne doar `cover_positional_bands` (curată / activă).
"""
from __future__ import annotations

from typing import Callable

import numpy as np


def _normalize(scores: dict[int, float], max_num: int) -> dict[int, float]:
    if not scores:
        return {n: 0.0 for n in range(1, max_num + 1)}
    vals = np.fromiter(scores.values(), dtype=np.float64)
    vmin, vmax = float(vals.min()), float(vals.max())
    rng = max(vmax - vmin, 1e-12)
    out = {int(k): float((v - vmin) / rng) for k, v in scores.items()}
    for n in range(1, max_num + 1):
        out.setdefault(n, 0.0)
    return out


def _binary(draws_2d: np.ndarray, max_num: int) -> np.ndarray:
    """(max_num, n_draws) indicator 0/1."""
    n = draws_2d.shape[0]
    B = np.zeros((max_num, n), dtype=np.float64)
    for i, row in enumerate(draws_2d):
        for v in row:
            vi = int(v)
            if 1 <= vi <= max_num:
                B[vi - 1, i] = 1.0
    return B


def score_cover_positional_bands(draws_2d: np.ndarray, max_num: int) -> dict[int, float]:
    """Acoperire pe benzi poziționale (decade): împarte [1..max_num] în 5 benzi egale
    și echilibrează selecția între ele. Numere din benzi sub-reprezentate în ponderile
    recente primesc un bonus → pool echilibrat pe tot intervalul."""
    n = draws_2d.shape[0]
    if n < 10:
        return {}
    B = _binary(draws_2d, max_num)
    rec = np.exp(np.linspace(-2.0, 0.0, n))
    freq_w = (B * rec[None, :]).sum(axis=1)
    n_bands = 5
    band_size = max_num / n_bands
    band_ids = (np.arange(max_num) / band_size).astype(int).clip(0, n_bands - 1)
    band_total = np.array([freq_w[band_ids == b].sum() + 1e-9 for b in range(n_bands)])
    band_avg = band_total / np.array([(band_ids == b).sum() + 1e-9 for b in range(n_bands)])
    band_inv = 1.0 / band_avg
    band_weight = band_inv[band_ids]
    scores = {i + 1: float(freq_w[i] * band_weight[i]) for i in range(max_num)}
    return _normalize(scores, max_num)


COVERAGE_METHODS: dict[str, tuple[Callable, str, bool, str]] = {
    "cover_positional_bands": (
        score_cover_positional_bands,
        "coverage",
        False,
        "acoperire benzi poziționale (decade) — echilibru pe tot intervalul · CPU",
    ),
}
