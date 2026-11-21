"""Batch the application's top-K rule with a maximum of two consecutive numbers."""

from __future__ import annotations

import numpy as np

from loto_enterprise.core.dynamic_pools import ranked_pools


def pool_numbers(scores: np.ndarray, k: int) -> np.ndarray:
    """Return one ascending, 1-based pool per row of finite scores (up to 63 balls)."""
    scores = np.asarray(scores)
    if scores.ndim != 2:
        raise ValueError("Expected a matrix of scores")
    return ranked_pools(scores, universe=scores.shape[1], pool_size=k, max_run=2)
