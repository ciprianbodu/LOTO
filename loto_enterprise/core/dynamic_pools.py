"""Batch pool selection for multiple scorers sharing a single draw history."""

from __future__ import annotations

import numpy as np

from loto_enterprise.core.pool_selection import select_pool_from_scores
from loto_enterprise.core.score_validation import SCORE_VARIANCE_EPSILON


def _below_free_scores(scores, adjusted, hit, counts):
    """Batch the engine's factor-zero ordering for rows containing zero scores."""
    values = scores[:, hit]
    # Dense ranks of the distinct (count ascending, score descending) pairs.
    order = np.lexsort((-values, np.broadcast_to(counts, values.shape)), axis=1)
    ordered = np.take_along_axis(values, order, axis=1)
    changed = (np.diff(counts[order]) != 0) | (ordered[:, 1:] != ordered[:, :-1])
    ranks = np.empty(values.shape, dtype=np.int16)
    ranks[:, 0] = 1
    ranks[:, 1:] = 1 + np.cumsum(changed, axis=1)
    dense = np.empty_like(ranks)
    np.put_along_axis(dense, order, ranks, axis=1)
    floor = np.zeros(len(scores))
    peak = np.max(adjusted, axis=1)
    if not hit.all():
        free = scores[:, ~hit]
        floor = np.min(free, axis=1)
        free_max = np.max(free, axis=1)
        # The effective factor puts every penalized score below the lowest
        # positive free score whenever a positive free score exists.
        peak = np.where(free_max > 0, free_max, peak)
    step = np.maximum(1.0, peak) * 1e-6
    return floor[:, None] - step[:, None] * dense


def _penalized(scores, past_draws, draws, factor):
    # Delay the engine import so its scorer can also import this module.
    from loto_engine import _ZERO_PENALTY_FACTOR, LotoEngine

    universe = scores.shape[1]
    labels = range(1, universe + 1)
    # With strictly positive factors each multiplier depends only on the
    # shared history. Use the engine even for the rounding of factor**count.
    multipliers, counts = LotoEngine.apply_recent_penalty(
        dict.fromkeys(labels, 1.0), past_draws, draws, factor, universe
    )
    if not counts:
        return scores
    scores = np.asarray(scores, dtype=np.float64)
    weights = np.fromiter(multipliers.values(), dtype=np.float64, count=universe)
    adjusted = scores * weights
    if factor <= 0:
        hit = np.array([label in counts for label in labels])
        zero = np.min(scores, axis=1) == 0
        if np.any(zero):
            counts_hit = np.array(
                [counts[label] for label in labels if label in counts]
            )
            adjusted[np.ix_(zero, hit)] = _below_free_scores(
                scores[zero], adjusted[zero], hit, counts_hit
            )
        # An exceptionally small positive score can change the factor itself.
        # Delegate those rows to retain the engine's exact power rounding.
        special = np.zeros(len(scores), dtype=bool)
        if not np.all(hit):
            free_min = np.min(scores[:, ~hit], axis=1)
            hit_max = np.max(scores[:, hit], axis=1)
            with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
                special = ~zero & (0.5 * free_min / hit_max < _ZERO_PENALTY_FACTOR)
        for row in np.flatnonzero(special):
            penalized, _ = LotoEngine.apply_recent_penalty(
                dict(zip(labels, scores[row], strict=True)),
                past_draws,
                draws,
                factor,
                universe,
            )
            adjusted[row] = list(penalized.values())
    flat = np.ptp(adjusted, axis=1) <= SCORE_VARIANCE_EPSILON
    adjusted[flat] = scores[flat]
    return adjusted


def ranked_pools(
    scores: np.ndarray,
    *,
    pool_size: int,
    universe: int,
    min_ball: int = 1,
    max_ball: int | None = None,
    max_run: int = 0,
    past_draws=None,
    penalty_draws: int = 0,
    penalty_factor: float = 0.5,
) -> np.ndarray:
    """One ascending 1-based pool per scorer; ties favor the larger label.

    Every row sees the same past_draws (chronological rows of 1-based ball
    labels). Supply nonnegative scores for the recent penalty and the effective
    interval after the pipeline has resolved narrow or reversed bounds.
    """
    scores = np.asarray(scores)
    if scores.ndim != 2 or scores.shape[1] != universe or not 1 <= universe <= 63:
        raise ValueError("Expected a matrix of scores for 1 to 63 balls")
    if scores.dtype.kind not in "biuf":
        raise ValueError("Expected real numeric scores")
    if scores.dtype.kind != "f" or scores.dtype.itemsize > 8:
        scores = scores.astype(np.float64)  # rank_by_score compares Python floats
    if not np.isfinite(scores).all():
        raise ValueError("Expected finite scores")
    lo = int(min_ball)
    hi = universe if max_ball is None else int(max_ball)
    width = hi - lo + 1
    k = int(pool_size)
    if not 1 <= lo <= hi <= universe or not 1 <= k <= width:
        raise ValueError("Expected a normalized interval that contains the pool")
    if (
        penalty_draws > 0
        and past_draws is not None
        and len(past_draws)
        and penalty_factor < 1
    ):
        if np.any(scores < 0):
            raise ValueError("Recent penalty requires nonnegative candidate scores")
        scores = _penalized(scores, past_draws, penalty_draws, penalty_factor)
    scores = scores[:, lo - 1 : hi]
    # Stable ascending sort reversed = higher number first at equal score.
    ranked = np.argsort(scores, axis=1, kind="stable")[:, ::-1]
    if max_run > 0:
        # At most width-k unused positions divide the chosen balls into
        # width-k+1 runs. Balanced runs attain this lower bound on the limit.
        slots = width - k + 1
        max_run = max(int(max_run), (k + slots - 1) // slots)
    if max_run <= 0 or max_run >= k:
        return (np.sort(ranked[:, :k], axis=1) + lo).astype(np.uint8)
    n_rows = len(scores)
    bits = np.left_shift(np.uint64(1), np.arange(width, dtype=np.uint64))
    chosen = np.zeros(n_rows, dtype=np.uint64)
    sizes = np.zeros(n_rows, dtype=np.uint8)
    for column in range(width):
        candidate = chosen | bits[ranked[:, column]]
        runs = candidate & (candidate >> 1)
        for shift in range(2, max_run + 1):
            runs &= candidate >> shift
        accept = (runs == 0) & (sizes < k)
        chosen = np.where(accept, candidate, chosen)
        sizes += accept
        if column + 1 >= k and np.all(sizes == k):
            break
    # A filled pool witnesses completion for every earlier greedy acceptance.
    # Where the scan gets stuck only the production look-ahead can decide.
    for row in np.flatnonzero(sizes != k):
        pool = select_pool_from_scores(
            dict(enumerate(scores[row], start=lo)),
            k,
            set(),
            max_num=hi,
            max_consecutive_run=max_run,
        )
        chosen[row] = np.bitwise_or.reduce(bits[np.asarray(pool) - lo])
    present = (chosen[:, None] & bits) != 0
    numbers = np.nonzero(present)[1].reshape(n_rows, k) + lo
    return numbers.astype(np.uint8)
