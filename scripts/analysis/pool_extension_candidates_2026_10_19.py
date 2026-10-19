"""The same sixteen causal adaptive scores, generalized to lottery geometry.

NEW research bank for Loto 5/40 (max_num=40, draw_n=6) and Joker Urna 1
(max_num=45, draw_n=5). Also supports 49/6 for exact parity verification against
the frozen original. Neither the frozen 6/49 bank nor production is modified.
No real outcomes, files, registry, selection decisions or pools are read here.
CPU NumPy/SciPy only. Formula parameters and sixteen names remain unchanged.

API
---
``candidate_specs(max_num, draw_n)`` returns sixteen JSON-friendly definitions.
``generate(data, expert_scores, *, max_num, draw_n)`` yields
``(name, family, score[T,max_num], fallback_mask[T])``. Larger scores are better.
``data`` contains sorted dates[N], draws[N,draw_n] of distinct labels 1..max_num,
targets[T] (strictly increasing raw-row indices), optional canonical groups[N]
and strict-day prefix cutoffs prior[N], and canonical frequency_past[T,max_num].
The latter equals expert_scores['frequency'] exactly. expert_scores also supplies
markov_lag2 and ewma_hl30 of the same finite shape, computed by the caller from
strictly earlier dates. Formula dependencies are parent_names; expert_names also
includes frequency as the common fallback source. Only actually consumed parents
propagate invalidity; merely having frequency as reserve does not invalidate an
otherwise usable BOCPD. The orchestrator owns that invalidity propagation.

Let V=max_num, d=draw_n, p0=d/V, and R(s)=(mean_rank(s)-1)/(V-1).

A: four exact independent-per-ball BOCPD models, daily hazard 1/128 or 1/512,
beta prior concentration20/100 with mean p0. No run-length cap or pruning.
A day of b draws provides each ball's count y via Beta-Binomial(b,y,a,beta).
Hazard is applied before the day's prediction; every prediction of that day
precedes all its updates. Counts/training begin at raw row zero. These are
marginal scoring models, not a joint lottery probability model. O(4*D^2*V).

B: four lag2 pair-interaction contrasts with shrink20/100 and amplitude .25/.5.
Every past transition u>=2 uses source draw[u-2] and response draw[u]. Forecast
at strict-day prefix n uses the choose(d,2) pairs of draw[n-2]. For each pair,
q_state,j=(C_state,j+shrink*p_j)/(n_state+shrink), where p_j is the response
marginal over all past lag2 transitions. The correction is the mean pair
contrast (q11+q00)-(q10+q01), accumulated with math.fsum, symmetric in pair order.
S=R(markov_lag2)+amplitude*(R(contrast)-.5). A contrast of span<=1e-12 contributes
zero. All raw history is learned, but only transitions whose response date is
strictly earlier can inform the current prediction. Lag2 counts raw rows, so
within-day order remains part of the historical lag definition for later days.

C: eight bilinear rank calibrators: Markov x frequency/EWMA30, grid3/5 nodes,
memory all/half-life256 target draws, prior exposure100 per node toward p0.
Posterior cell mean=(weighted hits+100*p0)/(weighted exposure+100); interpolate
using soft bilinear hats on exact mean ranks. Train ONLY the supplied target
stream (earlier cached expert scores do not exist). Predict the full day first;
then decay old learned counts by 2**(-b/256) and add that day's unweighted batch.
The prior does not decay. Half-rank coordinate denominator 2*(V-1) is reduced
with gcd against grid segments, avoiding truncation for V=40 and preserving
integer batch aggregation/equivariance. At V=49 the integer basis and all
arithmetic are exactly the original bank's basis. This is custom rank scoring,
not an implementation of BBQ or a calibrated joint lottery model.

All ties use mean ranks, without label jitter. Flat/non-finite final scores
fall back EXACTLY to canonical frequency, flagged in bool[T]. Input arrays are
not mutated. The runner alone applies top-K, consecutive constraints, selection
and inference; no candidate is promoted by this module. Complexity counts one
BOCPD model, two components for Markov+interaction, or two calibration parents.

Verify: python -m pytest -q test_pool_extension_candidates_2026_10_19.py
"""

from __future__ import annotations

import math
import sys
from collections.abc import Iterator
from itertools import combinations
from pathlib import Path

import numpy as np
from scipy.special import betaln, gammaln
from scipy.stats import rankdata

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from loto_enterprise.core.score_validation import SCORE_VARIANCE_EPSILON

BOCPD_HAZARD_DAYS = (128, 512)
PRIOR_STRENGTHS = (20, 100)
CONTRAST_AMPLITUDES = (0.25, 0.5)
CALIBRATION_PARTNERS = ("frequency", "ewma_hl30")
CALIBRATION_NODES = (3, 5)
CALIBRATION_MEMORIES = (None, 256)
CALIBRATION_PRIOR = 100.0
EXPERT_NAMES = ("frequency", "markov_lag2", "ewma_hl30")


def _geometry(max_num: int, draw_n: int) -> tuple[int, int, float]:
    """Validate the explicit game geometry, without inferring it from outcomes."""
    if (
        isinstance(max_num, (bool, np.bool_))
        or isinstance(draw_n, (bool, np.bool_))
        or not isinstance(max_num, (int, np.integer))
        or not isinstance(draw_n, (int, np.integer))
    ):
        raise TypeError("max_num and draw_n must be integers")
    max_num, draw_n = int(max_num), int(draw_n)
    if (max_num, draw_n) not in {(40, 6), (45, 5), (49, 6)}:
        raise ValueError("supported geometries are 40/6, 45/5 and 49/6")
    return max_num, draw_n, draw_n / max_num


def candidate_specs(max_num: int, draw_n: int) -> list[dict]:
    """Fixed parameters and formula descriptions, independent of all outcomes."""
    max_num, draw_n, p0 = _geometry(max_num, draw_n)
    active_pairs = math.comb(draw_n, 2)
    specs = []
    for hazard_days in BOCPD_HAZARD_DAYS:
        for strength in PRIOR_STRENGTHS:
            specs.append(
                {
                    "name": f"bocpd_daily_h{hazard_days}_k{strength}",
                    "family": "bocpd_beta_binomial_daily",
                    "complexity": 1,
                    "parent_names": [],
                    "expert_names": ["frequency"],
                    "params": {
                        "hazard_per_day": 1.0 / hazard_days,
                        "prior_strength": strength,
                        "prior_mean": p0,
                        "runlength_cap": None,
                        "per_ball_posterior": True,
                        "likelihood": "beta_binomial_per_day_block",
                        "prediction": "hazard_before_day; all rows before update",
                        "training": "full raw history from row zero",
                    },
                }
            )
    for strength in PRIOR_STRENGTHS:
        for amp in CONTRAST_AMPLITUDES:
            specs.append(
                {
                    "name": f"lag2_pair_contrast_k{strength}_a{int(amp * 100)}",
                    "family": "lag2_pair_interaction_contrast",
                    "complexity": 2,
                    "parent_names": ["markov_lag2"],
                    "expert_names": ["frequency", "markov_lag2"],
                    "params": {
                        "shrink": strength,
                        "amplitude": amp,
                        "source_lag_rows": 2,
                        "active_pairs": active_pairs,
                        "marginal": "response marginal over all past lag2 transitions",
                        "q": "(C_state,j + shrink*p_j)/(n_state + shrink)",
                        "contrast": f"fsum((q11+q00)-(q10+q01)) / {active_pairs}",
                        "formula": "R(markov_lag2) + amplitude*(R(contrast)-0.5)",
                        "R": f"(exact mean rank - 1)/{max_num - 1}; no label jitter",
                        "flat_contrast": "span<=1e-12: neutral term zero",
                        "training": "all transitions with target date strictly past",
                    },
                }
            )
    for partner in CALIBRATION_PARTNERS:
        for nodes in CALIBRATION_NODES:
            for half_life in CALIBRATION_MEMORIES:
                memory = "all" if half_life is None else f"h{half_life}"
                specs.append(
                    {
                        "name": f"rank_bilinear_markov_{partner}_g{nodes}_{memory}",
                        "family": "online_bilinear_rank_calibration",
                        "complexity": 2,
                        "parent_names": ["markov_lag2", partner],
                        "expert_names": list(
                            dict.fromkeys(("frequency", "markov_lag2", partner))
                        ),
                        "params": {
                            "nodes_per_axis": nodes,
                            "memory_half_life": half_life,
                            "memory_unit": "target draws, batched by date",
                            "prior_exposure_per_node": CALIBRATION_PRIOR,
                            "prior_mean": p0,
                            "formula": "sum(phi_node*(hits_node+100*p0)/(exposure_node+100))",
                            "basis": "bilinear hats on exact mean ranks in [0,1]",
                            "training": "provided target stream only; pre-day prediction",
                        },
                    }
                )
    for spec in specs:
        spec["geometry"] = {"max_num": max_num, "draw_n": draw_n}
    return specs


def _rank01(scores: np.ndarray) -> np.ndarray:
    return (rankdata(scores, axis=-1, method="average") - 1.0) / (
        scores.shape[-1] - 1.0
    )


def _validate(data: dict, expert_scores: dict, max_num: int, draw_n: int) -> tuple:
    raw_draws = np.asarray(data["draws"])
    if raw_draws.ndim != 2 or raw_draws.shape[1] != draw_n:
        raise ValueError(f"draws must have shape (N,{draw_n})")
    try:
        numeric = np.asarray(raw_draws, dtype=np.float64)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("draws must contain integer ball labels") from exc
    if (
        not np.isfinite(numeric).all()
        or np.any(numeric != np.floor(numeric))
        or np.any((numeric < 1) | (numeric > max_num))
    ):
        raise ValueError(f"draws must contain integer ball labels in 1..{max_num}")
    draws = numeric.astype(np.int64)
    if np.any(np.diff(np.sort(draws, axis=1), axis=1) == 0):
        raise ValueError(f"each draw must contain {draw_n} distinct balls")
    n = len(draws)
    if n == 0:
        raise ValueError("at least one raw history row is required")
    dates = np.asarray(data["dates"])
    if dates.shape != (n,):
        raise ValueError("dates must have shape (N,)")
    try:
        days = dates.astype("datetime64[D]")
    except (TypeError, ValueError) as exc:
        raise ValueError("dates must be valid chronological calendar dates") from exc
    if np.isnat(days).any() or np.any(days[1:] < days[:-1]):
        raise ValueError("dates must be valid chronological calendar dates")
    starts = np.r_[0, np.flatnonzero(days[1:] != days[:-1]) + 1]
    ends = np.r_[starts[1:], n]
    groups = np.repeat(np.arange(len(starts)), ends - starts)
    prior = starts[groups]
    if "groups" in data and not np.array_equal(data["groups"], groups):
        raise ValueError("groups must be canonical contiguous day IDs")
    if "prior" in data and not np.array_equal(data["prior"], prior):
        raise ValueError("prior must contain strict-day raw prefix cutoffs")
    raw_targets = np.asarray(data["targets"])
    if raw_targets.ndim != 1 or not np.issubdtype(raw_targets.dtype, np.integer):
        raise ValueError("targets must be a one-dimensional integer array")
    targets = raw_targets.astype(np.int64)
    if np.any((targets < 0) | (targets >= n)) or np.any(targets[1:] <= targets[:-1]):
        raise ValueError("targets must be strictly increasing raw-row indices")
    experts = {}
    for name in EXPERT_NAMES:
        values = np.asarray(expert_scores[name], dtype=np.float64)
        if values.shape != (len(targets), max_num) or not np.isfinite(values).all():
            raise ValueError(f"{name} must supply finite scores of shape (T,{max_num})")
        experts[name] = values
    frequency = np.asarray(data["frequency_past"], dtype=np.float64)
    if not np.array_equal(frequency, experts["frequency"]) or (
        len(targets) and np.any(np.ptp(frequency, axis=1) <= SCORE_VARIANCE_EPSILON)
    ):
        raise ValueError(
            "frequency_past must equal usable canonical frequency prefix scores"
        )
    actual = np.zeros((n, max_num), dtype=np.int64)
    actual[np.arange(n)[:, None], draws - 1] = 1
    positions = np.full(n, -1, dtype=np.int64)
    positions[targets] = np.arange(len(targets))
    return draws - 1, actual, starts, ends, targets, positions, experts


def _finished(
    scores: np.ndarray, frequency: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    finite = np.isfinite(scores).all(axis=1)
    mask = ~finite
    mask[finite] |= np.ptp(scores[finite], axis=1) <= SCORE_VARIANCE_EPSILON
    result = scores.copy()
    result[mask] = frequency[mask]
    return result, mask


def _beta_binomial_likelihood(
    a: np.ndarray, beta: np.ndarray, y: np.ndarray, trials: int
) -> np.ndarray:
    """Exact beta-binomial formulas for common 1/2-row dates, generic otherwise."""
    total = a + beta
    if trials == 1:
        return np.where(y == 1, a / total, beta / total)
    if trials == 2:
        denominator = total * (total + 1.0)
        return np.where(
            y == 2,
            a * (a + 1.0) / denominator,
            np.where(
                y == 1, 2.0 * a * beta / denominator, beta * (beta + 1.0) / denominator
            ),
        )
    log_comb = gammaln(trials + 1) - gammaln(y + 1) - gammaln(trials - y + 1)
    return np.exp(log_comb + betaln(a + y, beta + trials - y) - betaln(a, beta))


def _bocpd(
    actual: np.ndarray,
    starts: np.ndarray,
    ends: np.ndarray,
    targets: np.ndarray,
    positions: np.ndarray,
    p0: float,
) -> np.ndarray:
    max_num = actual.shape[1]
    d = len(starts)
    counts = np.zeros((d + 1, max_num), dtype=np.int64)
    trials = np.zeros(d + 1, dtype=np.int64)
    for day, (start, end) in enumerate(zip(starts, ends)):
        counts[day + 1] = counts[day] + actual[start:end].sum(axis=0)
        trials[day + 1] = trials[day] + end - start
    # Fixed-size storage retains every regime start, including tiny probabilities.
    weights = np.zeros(
        (2, 2, d, max_num), dtype=np.float64
    )  # strength,hazard,start,ball
    result = np.empty((2, 2, len(targets), max_num), dtype=np.float64)
    for day, (start, end) in enumerate(zip(starts, ends)):
        tpos = positions[start:end]
        tpos = tpos[tpos >= 0]
        y = counts[day + 1] - counts[day]
        b = int(end - start)
        for ki, strength in enumerate(PRIOR_STRENGTHS):
            if day == 0:
                if len(tpos):
                    result[ki, :, tpos, :] = p0
                weights[ki, :, 0, :] = 1.0
                continue
            regime_counts = counts[day] - counts[:day]
            regime_trials = (trials[day] - trials[:day])[:, None]
            a = strength * p0 + regime_counts
            beta = strength * (1.0 - p0) + regime_trials - regime_counts
            means = a / (a + beta)
            likelihood = _beta_binomial_likelihood(a, beta, y, b)
            reset_likelihood = _beta_binomial_likelihood(
                np.full(max_num, strength * p0),
                np.full(max_num, strength * (1.0 - p0)),
                y,
                b,
            )
            for hi, hazard_days in enumerate(BOCPD_HAZARD_DAYS):
                h = 1.0 / hazard_days
                old = weights[ki, hi, :day]
                if len(tpos):
                    prediction = (1.0 - h) * np.sum(old * means, axis=0) + h * p0
                    result[ki, hi, tpos] = prediction
                old *= (1.0 - h) * likelihood
                weights[ki, hi, day] = h * reset_likelihood
                normalizer = weights[ki, hi, : day + 1].sum(axis=0)
                weights[ki, hi, : day + 1] /= normalizer
    # Public spec order is hazard first, then strength.
    return result.transpose(1, 0, 2, 3).reshape(4, len(targets), max_num)


def _contrast_predictions(
    draws: np.ndarray,
    actual: np.ndarray,
    starts: np.ndarray,
    ends: np.ndarray,
    targets: np.ndarray,
    positions: np.ndarray,
    markov: np.ndarray,
) -> np.ndarray:
    max_num = actual.shape[1]
    active_pairs = math.comb(draws.shape[1], 2)
    pairs = np.asarray(list(combinations(range(max_num), 2)), dtype=np.int64)
    pair_index = np.full((max_num, max_num), -1, dtype=np.int64)
    pair_index[pairs[:, 0], pairs[:, 1]] = np.arange(len(pairs))
    pair_index[pairs[:, 1], pairs[:, 0]] = np.arange(len(pairs))
    source_count = np.zeros(max_num, dtype=np.int64)
    response_count = np.zeros(max_num, dtype=np.int64)
    source_response = np.zeros((max_num, max_num), dtype=np.int64)
    pair_count = np.zeros(len(pairs), dtype=np.int64)
    pair_response = np.zeros((len(pairs), max_num), dtype=np.int64)
    result = np.empty((4, len(targets), max_num), dtype=np.float64)
    markov_ranks = _rank01(markov)
    for start, end in zip(starts, ends):
        tpos = positions[start:end]
        tpos = tpos[tpos >= 0]
        if len(tpos):
            n = max(0, int(start) - 2)
            for ki, strength in enumerate(PRIOR_STRENGTHS):
                correction = np.zeros(max_num)
                if start >= 2 and n:
                    source = draws[start - 2]
                    aa, bb = np.asarray(list(combinations(source, 2))).T
                    ids = pair_index[aa, bb]
                    n11 = pair_count[ids]
                    n10 = source_count[aa] - n11
                    n01 = source_count[bb] - n11
                    n00 = n - source_count[aa] - source_count[bb] + n11
                    c11 = pair_response[ids]
                    c10 = source_response[aa] - c11
                    c01 = source_response[bb] - c11
                    c00 = (
                        response_count - source_response[aa] - source_response[bb] + c11
                    )
                    p = response_count / n
                    prior_hits = strength * p
                    q11 = (c11 + prior_hits) / (n11[:, None] + strength)
                    q10 = (c10 + prior_hits) / (n10[:, None] + strength)
                    q01 = (c01 + prior_hits) / (n01[:, None] + strength)
                    q00 = (c00 + prior_hits) / (n00[:, None] + strength)
                    terms = (q11 + q00) - (q10 + q01)
                    contrast = np.asarray(
                        [math.fsum(column) / active_pairs for column in terms.T]
                    )
                    if np.ptp(contrast) > SCORE_VARIANCE_EPSILON:
                        correction = _rank01(contrast) - 0.5
                for ai, amp in enumerate(CONTRAST_AMPLITUDES):
                    result[2 * ki + ai, tpos] = markov_ranks[tpos] + amp * correction
        # Every target of this date becomes available only after all predictions.
        for row in range(max(2, int(start)), int(end)):
            source, response = draws[row - 2], draws[row]
            aa, bb = np.asarray(list(combinations(source, 2))).T
            ids = pair_index[aa, bb]
            source_count[source] += 1
            response_count[response] += 1
            source_response[np.ix_(source, response)] += 1
            pair_count[ids] += 1
            pair_response[np.ix_(ids, response)] += 1
    return result


def _bilinear_basis(
    scores1: np.ndarray, scores2: np.ndarray, nodes: int
) -> tuple[np.ndarray, np.ndarray, int]:
    """Integer half-rank hat numerators; each ball's four weights sum to denom."""
    rank_denominator = 2 * (scores1.shape[-1] - 1)
    common = math.gcd(rank_denominator, nodes - 1)
    divisor = rank_denominator // common
    scale = (nodes - 1) // common
    x = np.rint(2.0 * (rankdata(scores1, axis=-1) - 1.0)).astype(np.int64)
    y = np.rint(2.0 * (rankdata(scores2, axis=-1) - 1.0)).astype(np.int64)
    x *= scale
    y *= scale
    lo_x = np.minimum(x // divisor, nodes - 2)
    lo_y = np.minimum(y // divisor, nodes - 2)
    fx, fy = x - lo_x * divisor, y - lo_y * divisor
    ids = np.stack(
        (
            lo_x * nodes + lo_y,
            (lo_x + 1) * nodes + lo_y,
            lo_x * nodes + lo_y + 1,
            (lo_x + 1) * nodes + lo_y + 1,
        ),
        axis=-1,
    )
    weights = np.stack(
        (
            (divisor - fx) * (divisor - fy),
            fx * (divisor - fy),
            (divisor - fx) * fy,
            fx * fy,
        ),
        axis=-1,
    )
    return ids, weights, divisor * divisor


def _calibration_predictions(
    actual: np.ndarray,
    starts: np.ndarray,
    ends: np.ndarray,
    targets: np.ndarray,
    positions: np.ndarray,
    experts: dict,
    p0: float,
) -> np.ndarray:
    max_num = actual.shape[1]
    result = np.empty((8, len(targets), max_num), dtype=np.float64)
    variant = 0
    for partner in CALIBRATION_PARTNERS:
        for nodes in CALIBRATION_NODES:
            ids, weights, denominator = _bilinear_basis(
                experts["markov_lag2"], experts[partner], nodes
            )
            exposure = np.zeros((2, nodes * nodes), dtype=np.float64)
            hits = np.zeros_like(exposure)
            for start, end in zip(starts, ends):
                tpos = positions[start:end]
                tpos = tpos[tpos >= 0]
                if not len(tpos):
                    continue
                raw = targets[tpos]
                for memory_index, half_life in enumerate(CALIBRATION_MEMORIES):
                    posterior = (hits[memory_index] + CALIBRATION_PRIOR * p0) / (
                        exposure[memory_index] + CALIBRATION_PRIOR
                    )
                    result[variant + memory_index, tpos] = (
                        np.sum(posterior[ids[tpos]] * weights[tpos], axis=-1)
                        / denominator
                    )
                    if half_life is not None:
                        decay = 2.0 ** (-len(tpos) / half_life)
                        exposure[memory_index] *= decay
                        hits[memory_index] *= decay
                # Integer sums avoid ball-order-dependent rounding of soft votes.
                batch_exposure = np.zeros(nodes * nodes, dtype=np.int64)
                batch_hits = np.zeros_like(batch_exposure)
                np.add.at(batch_exposure, ids[tpos].ravel(), weights[tpos].ravel())
                np.add.at(
                    batch_hits,
                    ids[tpos].ravel(),
                    (weights[tpos] * actual[raw, :, None]).ravel(),
                )
                exposure += batch_exposure / denominator
                hits += batch_hits / denominator
            variant += 2
    return result


def generate(
    data: dict, expert_scores: dict[str, np.ndarray], *, max_num: int, draw_n: int
) -> Iterator[tuple]:
    """Yield the fixed sixteen scores and exact frequency-fallback audit masks."""
    max_num, draw_n, p0 = _geometry(max_num, draw_n)
    draws, actual, starts, ends, targets, positions, experts = _validate(
        data, expert_scores, max_num, draw_n
    )
    specs = candidate_specs(max_num, draw_n)
    banks = (
        _bocpd(actual, starts, ends, targets, positions, p0),
        _contrast_predictions(
            draws, actual, starts, ends, targets, positions, experts["markov_lag2"]
        ),
        _calibration_predictions(actual, starts, ends, targets, positions, experts, p0),
    )
    index = 0
    for bank in banks:
        for raw_scores in bank:
            spec = specs[index]
            scores, mask = _finished(raw_scores, experts["frequency"])
            yield spec["name"], spec["family"], scores, mask
            index += 1
