"""Exact lag-two scorer, shared by the active catalog and historical replays."""

from __future__ import annotations

import numpy as np

from .methods_common import indicator, vector_to_scores


def _markov(draws_2d, max_num, lag: int):
    ind = indicator(draws_2d, max_num)
    n, m = ind.shape
    if n <= lag:
        return vector_to_scores(np.zeros(m), max_num)
    src_draw = ind[n - lag]
    # Only the source draw's rows contribute to the final product. Counts are
    # sums of binary indicators, so computing those rows alone is exact. Keep
    # the full final matrix/vector shapes to retain the same floating-point
    # reduction order (including score ties) as the dense reference.
    active = np.flatnonzero(src_draw)
    src = ind[:-lag, active]
    T = src.T @ ind[lag:]
    count = src.sum(axis=0)
    P = np.zeros((m, m))
    P[active] = T / np.maximum(count, 1.0)[:, None]
    return vector_to_scores(src_draw @ P, max_num)


def score_markov_lag2(draws_2d, max_num):
    """Tranziții de la extragerea de acum două pași (dependență cu lag 2)."""
    return _markov(draws_2d, max_num, lag=2)
