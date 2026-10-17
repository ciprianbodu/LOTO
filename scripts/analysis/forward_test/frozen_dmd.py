"""Copia ÎNGHEȚATĂ a metodei `dmd_forecast`, la data preînregistrării (2026-09-27).

Testul pe extrageri viitoare evaluează exact regula fixată azi. Metoda din
aplicație (`loto_enterprise/benchmark/methods_wave2.score_dmd_forecast`) se poate
schimba ulterior; copia de aici nu se modifică pe durata testului. Depinde doar
de numpy: indicatorul, normalizarea min-max și departajarea canonică (scor
descrescător, apoi numărul mai mare) sunt copiate aici, nu importate.
"""

from __future__ import annotations

import math

import numpy as np

WINDOW = 200
RANK = 6


def _indicator(draws_2d, max_num: int) -> np.ndarray:
    arr = np.asarray(draws_2d)
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    n = arr.shape[0]
    out = np.zeros((n, int(max_num)), dtype=np.float64)
    if n == 0 or arr.size == 0:
        return out
    vals = arr.astype(np.int64, copy=False)
    rows = np.repeat(np.arange(n), arr.shape[1])
    cols = vals.ravel() - 1
    ok = (cols >= 0) & (cols < int(max_num))
    out[rows[ok], cols[ok]] = 1.0
    return out


def _to_scores(vec, max_num: int) -> dict[int, float]:
    v = np.asarray(vec, dtype=np.float64).reshape(-1)
    if v.shape[0] != int(max_num):
        full = np.zeros(int(max_num), dtype=np.float64)
        full[: min(v.shape[0], int(max_num))] = v[: int(max_num)]
        v = full
    finite = np.isfinite(v)
    if not finite.any():
        return {i + 1: 0.0 for i in range(int(max_num))}
    vmin = float(v[finite].min())
    vmax = float(v[finite].max())
    rng = max(vmax - vmin, 1e-12)
    out = np.where(finite, (v - vmin) / rng, 0.0)
    return {i + 1: float(out[i]) for i in range(int(max_num))}


def scores(draws_2d, max_num: int) -> dict[int, float]:
    """DMD: A ≈ Y X⁺ pe matricea-indicator (numere × timp); scor = A · x_ultim."""
    ind = _indicator(draws_2d, max_num)
    x = ind[-WINDOW:].T
    m, n = x.shape
    if n < 20:
        return _to_scores(ind[-50:].mean(axis=0) if ind.shape[0] else np.zeros(m), max_num)
    x = x - x.mean(axis=1, keepdims=True)
    X, Y = x[:, :-1], x[:, 1:]
    U, s, Vt = np.linalg.svd(X, full_matrices=False)
    r = min(RANK, int((s > 1e-8).sum()))
    if r == 0:
        return _to_scores(np.zeros(m), max_num)
    Ur, sr, Vtr = U[:, :r], s[:r], Vt[:r]
    A_tilde = Ur.T @ Y @ Vtr.T / sr
    A = Ur @ A_tilde @ Ur.T
    return _to_scores(A @ x[:, -1], max_num)


def ranking(draws_2d, max_num: int) -> list[int]:
    """Clasamentul complet: scor descrescător, la egalitate numărul mai mare."""
    sc = scores(draws_2d, max_num)
    finite = [(n, s) for n, s in sc.items() if math.isfinite(s)]
    return [n for n, _ in sorted(finite, key=lambda kv: (kv[1], 0.0, kv[0]), reverse=True)]
