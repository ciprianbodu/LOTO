"""Utilitare comune pentru metodele de scoring (setul din 14.09.2026).

Contractul unei metode (CLAUDE.md §5.2):
    fn(draws_2d: np.ndarray, max_num: int) -> dict[int, float]
Scorurile sunt normalizate în [0, 1] prin min-max; orice valoare nefinită
devine 0. Metoda nu modifică `draws_2d` și nu ține stare între apeluri.

Aici stau, o singură dată, cele două primitive pe care vechile module le
copiau de 7 și respectiv 6 ori (audit 2026-09-14, §5.2): normalizarea și
matricea-indicator (n_extrageri × max_num).
"""

from __future__ import annotations

from typing import Callable

import numpy as np

# Tipul unei intrări de registry: (callable, familie, requires_train, note)
MethodTuple = tuple[Callable, str, bool, str]


def indicator(draws_2d: np.ndarray, max_num: int) -> np.ndarray:
    """Matrice (n, max_num) float64: 1.0 unde numărul j+1 a ieșit la extragerea i.

    Valorile din afara 1..max_num sunt ignorate (istoricul e validat înainte,
    dar metoda rămâne sigură pe apel direct).
    """
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


def vector_to_scores(vec: np.ndarray, max_num: int) -> dict[int, float]:
    """Vector de lungime max_num (index 0 = numărul 1) → dict normalizat min-max."""
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


def normalize(scores: dict[int, float], max_num: int) -> dict[int, float]:
    """Dict {număr: scor} → dict complet 1..max_num, normalizat min-max."""
    vec = np.zeros(int(max_num), dtype=np.float64)
    seen = np.zeros(int(max_num), dtype=bool)
    for k, val in (scores or {}).items():
        try:
            idx = int(k) - 1
            fv = float(val)
        except (TypeError, ValueError):
            continue
        if 0 <= idx < int(max_num):
            vec[idx] = fv if np.isfinite(fv) else np.nan
            seen[idx] = True
    if not seen.any():
        return {i + 1: 0.0 for i in range(int(max_num))}
    # Numerele fără scor primesc minimul (după normalizare: 0), ca înainte.
    finite = np.isfinite(vec) & seen
    fill = float(vec[finite].min()) if finite.any() else 0.0
    vec[~seen] = fill
    return vector_to_scores(vec, max_num)


def expected_rate(draws_2d: np.ndarray, max_num: int) -> float:
    """Probabilitatea de bază ca un număr dat să iasă la o extragere: draw_n/max_num."""
    arr = np.asarray(draws_2d)
    draw_n = arr.shape[1] if arr.ndim == 2 else 1
    return float(draw_n) / float(max(int(max_num), 1))


def ewma_weights(n: int, half_life: float) -> np.ndarray:
    """Ponderi 0.5^(vârstă/half_life), vârsta 0 = ultima extragere; sumă 1."""
    if n <= 0:
        return np.zeros(0, dtype=np.float64)
    ages = np.arange(n - 1, -1, -1, dtype=np.float64)
    w = np.power(0.5, ages / float(half_life))
    s = w.sum()
    return w / s if s > 0 else w


def last_seen_index(ind: np.ndarray) -> np.ndarray:
    """Pentru fiecare număr, indexul ultimei apariții (−1 dacă nu a apărut)."""
    n = ind.shape[0]
    if n == 0:
        return np.full(ind.shape[1], -1, dtype=np.int64)
    idx = np.where(ind > 0, np.arange(n, dtype=np.int64)[:, None], -1)
    return idx.max(axis=0)


def make_registry(entries: list[tuple[str, Callable, str, str]]) -> dict[str, MethodTuple]:
    """(nume, fn, familie, note) → dict de registry, cu numele verificate unice."""
    out: dict[str, MethodTuple] = {}
    for name, fn, family, notes in entries:
        if name in out:
            raise ValueError(f"nume de metodă duplicat în modul: {name}")
        out[name] = (fn, family, False, notes)
    return out


__all__ = [
    "MethodTuple",
    "ewma_weights",
    "expected_rate",
    "indicator",
    "last_seen_index",
    "make_registry",
    "normalize",
    "vector_to_scores",
]
