"""Normalizare canonică a scorurilor — [0, 1], univers complet, valori finite.

Un singur NaN/inf în dict otrăvea vmin/vmax (tot dict-ul devenea NaN) iar
`rank_by_score` cădea pe ordinea de inserare. Copiile locale din modulele de
metode divergeau: graph/search_649 filtrau finitele, restul nu.

Contract (identic cu vechiul `_normalize` pe scoruri TOATE finite):
    * dict gol → {1..max_num: 0.0}
    * min-max pe valorile FINITE; non-finitele ies 0.0 (ultimele în ranking)
    * numerele lipsă din 1..max_num primesc 0.0
"""
from __future__ import annotations

import math

import numpy as np


def normalize_scores(scores: dict[int, float], max_num: int) -> dict[int, float]:
    """Min-max [0, 1] pe 1..max_num. Non-finite → 0.0; lipsă → 0.0.

    Pe un dict cu toate valorile finite rezultatul e bit-identic cu vechile
    copii din methods*.py (aceeași formulă vmin/vmax/rng).
    """
    if not scores:
        return {n: 0.0 for n in range(1, max_num + 1)}
    vals = np.fromiter((float(v) for v in scores.values()), dtype=np.float64)
    finite = vals[np.isfinite(vals)]
    if finite.size == 0:
        return {n: 0.0 for n in range(1, max_num + 1)}
    vmin, vmax = float(finite.min()), float(finite.max())
    rng = max(vmax - vmin, 1e-12)
    out: dict[int, float] = {}
    for k, v in scores.items():
        fv = float(v)
        out[int(k)] = (fv - vmin) / rng if math.isfinite(fv) else 0.0
    for n in range(1, max_num + 1):
        out.setdefault(n, 0.0)
    return out
