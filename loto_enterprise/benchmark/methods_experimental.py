"""Explicit interval filters for research benchmarks, excluded from production.

``interval_extrema_k16`` reproduces the frozen conditional-extrema experiment:
a pool of 16, frequency over 50 prior draws, a 200-draw warmup, and interval
selection on the preceding 300 targets with a 50-observation shrinkage prior.
It is a structural filter, not evidence of a predictive advantage. Its 4+
objective is defined for pool 16; benchmarks at another pool size test a
different use of the same ranking, not a separately optimized selector.

The input is the strict prefix before the target day. ``history_cutoffs[i]``
identifies the first row of the day containing row i. Without dates, every
row is a separate day. No history, fitted values, or caches persist globally.
"""

from __future__ import annotations

from math import comb

import numpy as np

from loto_enterprise.core.ranking import rank_by_score

from .methods_common import make_registry, vector_to_scores

_POOL = 16
_WARMUP = 200
_FREQUENCY_WINDOW = 50
_ROLLING_WINDOW = 300
_STATE_PRIOR = 50


def _validated_inputs(draws_2d, max_num, history_cutoffs):
    """Reject malformed draws and inconsistent contiguous-day boundaries."""
    try:
        numeric_max = float(max_num)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("max_num must be a positive integer") from exc
    if (
        isinstance(max_num, (bool, np.bool_))
        or not np.isfinite(numeric_max)
        or not numeric_max.is_integer()
        or numeric_max < 1
    ):
        raise ValueError("max_num must be a positive integer")
    max_num = int(numeric_max)
    try:
        raw = np.asarray(draws_2d)
        if raw.ndim == 1 and not raw.size:
            raw = raw.reshape(0, 0)
        if raw.ndim != 2 or raw.dtype.kind in "bc":
            raise ValueError("draws must be a two-dimensional integer matrix")
        numeric = np.asarray(raw, dtype=np.float64)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("draws must be a two-dimensional integer matrix") from exc
    if (
        (len(numeric) and numeric.shape[1] == 0)
        or numeric.shape[1] > max_num
        or not np.isfinite(numeric).all()
        or np.any(numeric != np.floor(numeric))
        or np.any((numeric < 1) | (numeric > max_num))
    ):
        raise ValueError("draws must contain distinct integers in 1..max_num")
    draws = numeric.astype(np.int64)
    if draws.shape[1] and np.any(np.diff(np.sort(draws, axis=1), axis=1) == 0):
        raise ValueError("draws must contain distinct integers in 1..max_num")

    n = len(draws)
    if history_cutoffs is None:
        return draws, max_num, np.arange(n, dtype=np.int64)
    try:
        supplied = np.asarray(history_cutoffs)
        if supplied.ndim != 1 or supplied.dtype.kind in "bc":
            raise ValueError("history_cutoffs must be a one-dimensional integer sequence")
        if not supplied.size:
            return draws, max_num, np.arange(n, dtype=np.int64)
        numeric_cuts = np.asarray(supplied, dtype=np.float64)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("history_cutoffs must be a one-dimensional integer sequence") from exc
    if (
        len(numeric_cuts) != n
        or not np.isfinite(numeric_cuts).all()
        or np.any(numeric_cuts != np.floor(numeric_cuts))
        or np.any(numeric_cuts < 0)
        or np.any(numeric_cuts > np.arange(n))
        or np.any(np.diff(numeric_cuts) < 0)
    ):
        raise ValueError("history_cutoffs must give contiguous day starts for every row")
    cuts = numeric_cuts.astype(np.int64)
    if np.any(cuts[cuts] != cuts):
        raise ValueError("history_cutoffs must give contiguous day starts for every row")
    return draws, max_num, cuts


def _previous_state(minimum, maximum, cuts, cutoff, median_min, median_max):
    """Extrema of the last complete day strictly before this cutoff."""
    day_start = int(cuts[cutoff - 1])
    return int(minimum[day_start:cutoff].mean() <= median_min) + 2 * int(
        maximum[day_start:cutoff].mean() >= median_max
    )


def score_interval_extrema_k16(
    draws_2d, max_num: int, *, history_cutoffs=None
) -> dict[int, float]:
    """Rank F50 inside the selected interval first, with canonical score ties.

    Inputs must end before the prediction's day; cutoffs describe only this
    prefix. Malformed inputs raise ``ValueError``. Unsupported geometries or
    no evaluable training targets return flat scores, never a hidden fallback.
    """
    draws, max_num, cuts = _validated_inputs(draws_2d, max_num, history_cutoffs)
    n, draw_n = draws.shape
    flat = {number: 0.0 for number in range(1, max_num + 1)}
    if max_num < _POOL or draw_n < 4 or n <= _WARMUP:
        return flat
    targets = np.arange(max(0, n - _ROLLING_WINDOW), n)
    targets = targets[cuts[targets] >= _WARMUP]
    if not len(targets):
        return flat

    nums = np.arange(1, max_num + 1)
    # Tie-break fixed by the protocol: widest interval, then lowest bound.
    intervals = np.asarray(
        [(low, low + width - 1)
         for width in range(max_num, _POOL - 1, -1)
         for low in range(1, max_num - width + 2)],
        dtype=np.int64,
    )
    eligible = (nums[None, :] >= intervals[:, 0, None]) & (
        nums[None, :] <= intervals[:, 1, None]
    )
    indicator = np.zeros((n, max_num), dtype=np.int8)
    indicator[np.arange(n)[:, None], draws - 1] = 1
    cumulative = np.vstack(
        [np.zeros((1, max_num), dtype=np.int64), np.cumsum(indicator, axis=0)]
    )
    minimum, maximum = draws.min(axis=1), draws.max(axis=1)
    total = comb(max_num, draw_n)
    choose = lambda size: comb(size, draw_n) if size >= draw_n else 0
    median_min = next(x for x in nums if 1 - choose(max_num - int(x)) / total >= .5)
    median_max = next(x for x in nums if choose(int(x)) / total >= .5)
    successes = np.zeros(len(intervals), dtype=np.int64)
    by_state = np.zeros((4, len(intervals)), dtype=np.int64)
    state_counts = np.zeros(4, dtype=np.int64)

    # Only the last 300 target rows are evaluated, using their GLOBAL cutoffs.
    # Same-day rows never enter their frequency ranking or preceding-day state.
    for target in targets:
        cutoff = int(cuts[target])
        frequency = cumulative[cutoff] - cumulative[max(0, cutoff - _FREQUENCY_WINDOW)]
        rank = np.asarray(
            rank_by_score({int(x): float(frequency[x - 1]) for x in nums}, max_num)
        ) - 1
        members = eligible[:, rank]
        selected = members & (np.cumsum(members, axis=1) <= _POOL)
        hit4 = (selected * indicator[target, rank]).sum(axis=1) >= 4
        state = _previous_state(minimum, maximum, cuts, cutoff, median_min, median_max)
        successes += hit4
        by_state[state] += hit4
        state_counts[state] += 1

    state = _previous_state(minimum, maximum, cuts, n, median_min, median_max)
    objective = (by_state[state] + _STATE_PRIOR * successes / len(targets)) / (
        state_counts[state] + _STATE_PRIOR
    )
    selected_interval = eligible[int(np.argmax(objective))]
    frequency = cumulative[n] - cumulative[max(0, n - _FREQUENCY_WINDOW)]
    # This encodes only interval membership and the unmodified F50 levels.
    # A full-window count is at most 50, so every inside score exceeds every
    # outside score. Equal frequencies within either class remain equal.
    scores = frequency + (_FREQUENCY_WINDOW + 1) * selected_interval
    return vector_to_scores(scores, max_num)


score_interval_extrema_k16._uses_history_cutoffs = True

EXPERIMENTAL_METHODS = make_registry([
    (
        "interval_extrema_k16",
        score_interval_extrema_k16,
        "experimental_interval",
        "Filtru experimental de interval, pool 16, țintă 4+, condiționat de "
        "extremele ultimei zile; benchmark exclusiv, interzis în producție.",
    ),
])

__all__ = ["EXPERIMENTAL_METHODS", "score_interval_extrema_k16"]
