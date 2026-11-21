"""Replay the registered reference scorers for the 1,000-configuration study."""

from __future__ import annotations

import numpy as np

from scripts.analysis.legacy_methods_2026_11_20 import call_method
from loto_enterprise.core.pool_selection import select_pool_from_scores
from loto_enterprise.core.score_validation import has_usable_score_variance
from scripts.analysis.lr_pair_mixture_2026_11_18 import GAMES as PREVIOUS_STUDY


def reference_pools(
    game_key, matrix, cutoffs, indices, pool_n, universe, *, methods=None
):
    """Return ascending, one-based pools, in the requested draw-index order.

    The previous study recorded the incumbent for each played LR geometry.
    Frequency uses the registry's exponential decay. The registered random
    control derives its seed from the same integer history as the application.
    """
    if methods is None:
        methods = ["frequency", "random"]
        for key, _, max_num, _, size, _, incumbent in PREVIOUS_STUDY:
            if (key, max_num, size) == (game_key, universe, pool_n):
                methods.append(incumbent)
                break

    # Production converts its validated draw matrix to int64 before scoring.
    # This also matters for random, whose seed hashes the bytes of the prefix.
    matrix = np.asarray(matrix, dtype=np.int64)
    indices = np.asarray(indices, dtype=np.intp)
    cutoffs = np.asarray(cutoffs, dtype=np.intp)
    if indices.ndim != 1 or cutoffs.ndim != 1 or len(cutoffs) != len(matrix):
        raise ValueError("Expected aligned draw history and cutoff vector")
    if np.any(indices < 0) or np.any(indices >= len(matrix)):
        raise ValueError("Draw index outside history")
    boundaries = cutoffs[indices]
    if np.any(boundaries < 0) or np.any(boundaries > indices):
        raise ValueError("A training prefix may not include its target")
    days, inverse = np.unique(boundaries, return_inverse=True)
    by_day = {name: np.empty((len(days), pool_n), dtype=np.uint8) for name in methods}
    for i, cut in enumerate(days):
        history = matrix[:cut]
        for name, pools in by_day.items():
            scores, _ = call_method(
                name, history, universe, history_cutoffs=cutoffs[:cut]
            )
            if not has_usable_score_variance(scores):
                raise ValueError(
                    f"Unusable reference scores for {game_key}, {name}, prefix {cut}"
                )
            pools[i] = select_pool_from_scores(
                scores, pool_n, set(), max_num=universe, max_consecutive_run=2
            )
    return {name: pools[inverse] for name, pools in by_day.items()}
