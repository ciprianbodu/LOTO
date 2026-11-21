"""Three fixed, regularized scorers for the next draw, given its strict history.

On a date with multiple draws each ball's observation is the fraction of those
draws containing it. A day contributes one unit of evidence. A ball observed
at any time during that day can condition the next day's forecast.
"""

from __future__ import annotations

import numpy as np

from .methods_common import indicator, make_registry

_MAX_DAYS = 384


def _daily_rates(draws_2d, max_num, history_cutoffs):
    """Build up to 384 complete daily observations and the fair inclusion rate."""
    if (
        isinstance(max_num, (bool, np.bool_))
        or not isinstance(max_num, (int, np.integer))
        or max_num < 1
    ):
        raise ValueError("max_num must be a positive integer")
    draws = np.asarray(draws_2d)
    if draws.ndim == 1 and draws.size == 0:
        draws = draws.reshape(0, 0)
    if (
        draws.ndim != 2
        or draws.shape[1] > max_num
        or (len(draws) and not draws.shape[1])
    ):
        raise ValueError("draws must be a matrix with one column per extracted ball")
    fair = (draws.shape[1] or 1) / max_num
    n = len(draws)
    starts = np.arange(n)
    if history_cutoffs is not None:
        bounds = np.asarray(history_cutoffs)
        if bounds.shape != (n,) or (n and bounds.dtype.kind not in "iu"):
            raise ValueError(
                "history_cutoffs must contain one integer day start per draw"
            )
        expected = np.maximum.accumulate(np.where(bounds == starts, starts, -1))
        if not np.array_equal(bounds, expected) or (n and bounds[0] != 0):
            raise ValueError("history_cutoffs must identify consecutive complete days")
        starts = starts[bounds == starts]
    if not n:
        return np.empty((0, max_num), dtype=float), fair

    starts = starts[-_MAX_DAYS:]
    recent = draws[starts[0] :]
    if (
        recent.dtype.kind not in "iuf"
        or not np.all((recent >= 1) & (recent <= max_num))
        or (recent.dtype.kind == "f" and np.any(recent != np.floor(recent)))
        or np.any(np.diff(np.sort(recent, axis=1), axis=1) == 0)
    ):
        raise ValueError("draws must contain distinct integer balls in 1..max_num")
    hits = indicator(recent, max_num)
    if len(starts) == len(recent):
        return hits, fair
    sizes = np.diff(np.append(starts, n))
    return np.add.reduceat(hits, starts - starts[0], axis=0) / sizes[:, None], fair


def _marginal(rates, fair):
    # Two prior hits per number, with the corresponding number of fair draws.
    return (rates.sum(axis=0) + 2.0) / (len(rates) + 2.0 / fair)


def _scores(values):
    return dict(enumerate(values.tolist(), start=1))


def score_compact_multiscale_shrink(draws_2d, max_num, *, history_cutoffs=None):
    """Shrink three disjoint frequency estimates according to their cross-ball dispersion."""
    rates, fair = _daily_rates(draws_2d, max_num, history_cutoffs)
    score = np.full(max_num, fair)
    displacement = np.zeros(max_num)
    total_weight = 0
    # Short, medium and long observations have no draws in common.
    for start, stop, weight in ((0, 24, 3), (24, 96, 2), (96, _MAX_DAYS, 1)):
        block = rates[max(0, len(rates) - stop) : max(0, len(rates) - start)]
        n = len(block)
        if not n:
            continue
        delta = block.mean(axis=0) - fair
        spread = float(np.mean(delta * delta))
        sampling_variance = fair * (1.0 - fair) / n
        # The prior contributes two fair hits per number. A block with little
        # between-ball variation relative to sampling noise shrinks further.
        if spread:
            evidence = spread / (spread + sampling_variance)
            displacement += weight * evidence * (n / (n + 2.0 / fair)) * delta
        total_weight += weight
    if total_weight:
        score += displacement / total_weight
    return _scores(score)


def score_compact_transition_prior(draws_2d, max_num, *, history_cutoffs=None):
    """Predict from the previous day's balls, shrinking each conditional row to the marginal."""
    rates, fair = _daily_rates(draws_2d, max_num, history_cutoffs)
    prior = _marginal(rates, fair)
    if len(rates) < 2:
        return _scores(prior)
    # Only the contexts actually present on the last complete date are needed.
    contexts = (rates[:-1, rates[-1] > 0] > 0).astype(float)
    exposure = contexts.sum(axis=0)[:, None]
    joint = contexts.T @ rates[1:]
    prior_trials = 4.0 / fair
    conditional = (joint + prior_trials * prior) / (exposure + prior_trials)
    return _scores(conditional.mean(axis=0))


def score_compact_renewal_hazard(draws_2d, max_num, *, history_cutoffs=None):
    """Estimate a ball's hit rate at its current age, counting every day at risk."""
    rates, fair = _daily_rates(draws_2d, max_num, history_cutoffs)
    prior = _marginal(rates, fair)
    if not len(rates):
        return _scores(prior)
    day = np.arange(len(rates))[:, None]
    last = np.maximum.accumulate(np.where(rates > 0, day, -1), axis=0)
    # Exposure at t depends exclusively on appearances strictly before t.
    # Omit the left-censored interval until the first observed appearance.
    before = np.vstack((np.full((1, max_num), -1), last[:-1]))
    limits = np.ceil(np.array([0.5, 1.0, 2.0]) / fair)
    past_bins = np.searchsorted(limits, day - 1 - before, side="right")
    current_bins = np.searchsorted(limits, len(rates) - 1 - last[-1], side="right")
    at_risk = (before >= 0) & (past_bins == current_bins)
    prior_trials = 4.0 / fair
    hazard = ((rates * at_risk).sum(axis=0) + prior_trials * prior) / (
        at_risk.sum(axis=0) + prior_trials
    )
    # Independent age estimates need not sum to the drawn width. Rescale
    # positives or their complements to enforce the geometry and keep [0, 1].
    drawn = fair * max_num
    mass = hazard.sum()
    if mass > drawn:
        hazard *= drawn / mass
    elif mass < drawn:
        hazard += (1.0 - hazard) * ((drawn - mass) / (max_num - mass))
    return _scores(hazard)


score_compact_multiscale_shrink._uses_history_cutoffs = True
score_compact_transition_prior._uses_history_cutoffs = True
score_compact_renewal_hazard._uses_history_cutoffs = True

COMPACT_METHODS = make_registry(
    [
        (
            "compact_multiscale_shrink",
            score_compact_multiscale_shrink,
            "recency",
            "Frecvențe pe trei intervale disjuncte, regularizate după variație și volumul de date",
        ),
        (
            "compact_transition_prior",
            score_compact_transition_prior,
            "transition",
            "Tranziții între zile complete, regularizate către probabilitatea marginală a fiecărei bile",
        ),
        (
            "compact_renewal_hazard",
            score_compact_renewal_hazard,
            "gap",
            "Probabilitate de reapariție pe grupe de vârstă, cu zile la risc și prior marginal",
        ),
    ]
)
