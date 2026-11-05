"""Sixteen preregistered causal research scores for 6/49, 2026-10-19.

Research only: no files, registry, production decisions, pools or external data
are read. This bank does not choose a winner. CPU NumPy/SciPy only; no GPU/JIT.

API
---
``candidate_specs()`` returns 16 JSON-friendly name/family/params/complexity
records. ``generate(data, expert_scores)`` yields
``(name, family, scores[T,49], fallback_mask[T])``. Larger scores are better.
``data`` contains chronological dates[N], valid draws[N,6], targets[T] (strictly
increasing raw-row indices), optionally canonical groups[N]/prior[N], and
frequency_past[T,49]. ``expert_scores`` supplies frequency, markov_lag2 and
ewma_hl30, each finite shape[T,49], computed on strictly earlier dates.
frequency_past must equal the supplied canonical frequency scores exactly.
Targets can be any subset; the study orchestrator uses the second half of dates.
The full draw history starts at row zero; BOCPD and interactions learn from it.
Calibrators learn only the provided targets, since earlier expert scores do not
exist. All predictions of a date precede every outcome update for that date.

Family A: four exact independent-per-ball Bayesian online change-point models.
Daily hazard h=1/128 or 1/512; beta prior concentration k=20 or 100, mean p0=6/49.
Every earlier daily regime start remains in the run-length posterior (no cap or
pruning). Before a date, new-regime mass h predicts p0 and continuation mass
1-h predicts the posterior-weighted beta means. The day's b draws supply each
ball's count y, with Beta-Binomial(b,y,a,beta) likelihood; posterior regime
weights update AFTER emitting every prediction of the day. There is no hidden
change point between two draws on one date. These are marginal scoring models,
not a joint probability model for the lottery. Cost O(4*D^2*49), memory O(D*49).

Family B: four true two-antecedent lag-2 interactions, added to Markov ranks.
For each strictly past transition u>=2, source=draw[u-2], target=draw[u]. For
forecast day with raw prefix n, use the 15 pairs of draw[n-2]. For each pair
(a,b), source states 11/10/01/00 have response counts C_s,j and exposures n_s.
With p_j the response marginal over all n-2 past transitions (p0 when empty),
q_s,j=(C_s,j+k*p_j)/(n_s+k), k=20 or 100. Define
 d_j=mean_(15 pairs) [(q_11,j+q_00,j)-(q_10,j+q_01,j)]
 S_j=R(markov_lag2)_j + amp*(R(d)_j-.5), amp=.25 or .5,
where R is exact mean rank scaled by (rank-1)/48. The contrast is symmetric in
a,b and math.fsum makes pair accumulation independent of label traversal order.
A globally flat contrast (span<=1e-12) contributes zero, without amplifying
numerical noise. Lag two counts raw rows (the same convention as markov_lag2),
while availability is restricted to dates. Within-day draw order is therefore
part of the lag definition, not claimed irrelevant for later days.

Family C: eight online bilinear rank calibrators. Parents are Markov and either
frequency or EWMA30. Grid 3 or 5 equally spaced nodes per rank axis; soft
bilinear corner weights. Each node has prior pseudo-exposure 100, mean p0.
Node means=(weighted hits+100*p0)/(weighted exposures+100); ball score is its
bilinear interpolation of node means. Memory all or half-life256 TARGET DRAWS.
A date's b target rows are predicted together, then old learned exposure/hits
are multiplied by 2**(-b/256), then that date's unweighted batch is added.
The prior is not decayed. Half-rank units yield integer interpolation weights
and integer batch sums before division, preserving label equivariance even
with partial ties. Each prior is per node, not shared across the grid.

All score ties use mean ranks without ball-label jitter. The runner alone
applies canonical top-K/consecutive limits. A non-finite or flat final row
(span<=1e-12) falls back EXACTLY to supplied canonical frequency, with its bool
mask marked true. Inputs are not changed. Complexity counts direct formula
components (one BOCPD model; two for Markov+interaction or two score parents for
calibration), excluding the common flat guard. None is a production promotion.

Verify: python -m pytest -q test_pool_adaptive_candidates_2026_10_19.py
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

P0 = 6.0 / 49.0
BOCPD_HAZARD_DAYS = (128, 512)
PRIOR_STRENGTHS = (20, 100)
CONTRAST_AMPLITUDES = (0.25, 0.5)
CALIBRATION_PARTNERS = ("frequency", "ewma_hl30")
CALIBRATION_NODES = (3, 5)
CALIBRATION_MEMORIES = (None, 256)
CALIBRATION_PRIOR = 100.0
EXPERT_NAMES = ("frequency", "markov_lag2", "ewma_hl30")
PAIRS = np.asarray(list(combinations(range(49), 2)), dtype=np.int64)
PAIR_INDEX = np.full((49, 49), -1, dtype=np.int64)
PAIR_INDEX[PAIRS[:, 0], PAIRS[:, 1]] = np.arange(len(PAIRS))
PAIR_INDEX[PAIRS[:, 1], PAIRS[:, 0]] = np.arange(len(PAIRS))


def candidate_specs() -> list[dict]:
    """Fixed parameters and formula descriptions, independent of all outcomes."""
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
                        "prior_mean": P0,
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
                        "active_pairs": 15,
                        "marginal": "response marginal over all past lag2 transitions",
                        "q": "(C_state,j + shrink*p_j)/(n_state + shrink)",
                        "contrast": "fsum((q11+q00)-(q10+q01)) / 15",
                        "formula": "R(markov_lag2) + amplitude*(R(contrast)-0.5)",
                        "R": "(exact mean rank - 1)/48; no label jitter",
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
                            "prior_mean": P0,
                            "formula": "sum(phi_node*(hits_node+100*p0)/(exposure_node+100))",
                            "basis": "bilinear hats on exact mean ranks in [0,1]",
                            "training": "provided target stream only; pre-day prediction",
                        },
                    }
                )
    return specs


def _rank01(scores: np.ndarray) -> np.ndarray:
    return (rankdata(scores, axis=-1, method="average") - 1.0) / 48.0


def _validate(data: dict, expert_scores: dict) -> tuple:
    raw_draws = np.asarray(data["draws"])
    if raw_draws.ndim != 2 or raw_draws.shape[1] != 6:
        raise ValueError("draws must have shape (N,6)")
    try:
        numeric = np.asarray(raw_draws, dtype=np.float64)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("draws must contain integer ball labels") from exc
    if (
        not np.isfinite(numeric).all()
        or np.any(numeric != np.floor(numeric))
        or np.any((numeric < 1) | (numeric > 49))
    ):
        raise ValueError("draws must contain integer ball labels in 1..49")
    draws = numeric.astype(np.int64)
    if np.any(np.diff(np.sort(draws, axis=1), axis=1) == 0):
        raise ValueError("each draw must contain six distinct balls")
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
        if values.shape != (len(targets), 49) or not np.isfinite(values).all():
            raise ValueError(f"{name} must supply finite scores of shape (T,49)")
        experts[name] = values
    frequency = np.asarray(data["frequency_past"], dtype=np.float64)
    if not np.array_equal(frequency, experts["frequency"]) or (
        len(targets) and np.any(np.ptp(frequency, axis=1) <= SCORE_VARIANCE_EPSILON)
    ):
        raise ValueError(
            "frequency_past must equal usable canonical frequency prefix scores"
        )
    actual = np.zeros((n, 49), dtype=np.int64)
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
) -> np.ndarray:
    d = len(starts)
    counts = np.zeros((d + 1, 49), dtype=np.int64)
    trials = np.zeros(d + 1, dtype=np.int64)
    for day, (start, end) in enumerate(zip(starts, ends)):
        counts[day + 1] = counts[day] + actual[start:end].sum(axis=0)
        trials[day + 1] = trials[day] + end - start
    # Fixed-size storage retains every regime start, including tiny probabilities.
    weights = np.zeros((2, 2, d, 49), dtype=np.float64)  # strength,hazard,start,ball
    result = np.empty((2, 2, len(targets), 49), dtype=np.float64)
    for day, (start, end) in enumerate(zip(starts, ends)):
        tpos = positions[start:end]
        tpos = tpos[tpos >= 0]
        y = counts[day + 1] - counts[day]
        b = int(end - start)
        for ki, strength in enumerate(PRIOR_STRENGTHS):
            if day == 0:
                if len(tpos):
                    result[ki, :, tpos, :] = P0
                weights[ki, :, 0, :] = 1.0
                continue
            regime_counts = counts[day] - counts[:day]
            regime_trials = (trials[day] - trials[:day])[:, None]
            a = strength * P0 + regime_counts
            beta = strength * (1.0 - P0) + regime_trials - regime_counts
            means = a / (a + beta)
            likelihood = _beta_binomial_likelihood(a, beta, y, b)
            reset_likelihood = _beta_binomial_likelihood(
                np.full(49, strength * P0), np.full(49, strength * (1.0 - P0)), y, b
            )
            for hi, hazard_days in enumerate(BOCPD_HAZARD_DAYS):
                h = 1.0 / hazard_days
                old = weights[ki, hi, :day]
                if len(tpos):
                    prediction = (1.0 - h) * np.sum(old * means, axis=0) + h * P0
                    result[ki, hi, tpos] = prediction
                old *= (1.0 - h) * likelihood
                weights[ki, hi, day] = h * reset_likelihood
                normalizer = weights[ki, hi, : day + 1].sum(axis=0)
                weights[ki, hi, : day + 1] /= normalizer
    # Public spec order is hazard first, then strength.
    return result.transpose(1, 0, 2, 3).reshape(4, len(targets), 49)


def _contrast_predictions(
    draws: np.ndarray,
    actual: np.ndarray,
    starts: np.ndarray,
    ends: np.ndarray,
    targets: np.ndarray,
    positions: np.ndarray,
    markov: np.ndarray,
) -> np.ndarray:
    source_count = np.zeros(49, dtype=np.int64)
    response_count = np.zeros(49, dtype=np.int64)
    source_response = np.zeros((49, 49), dtype=np.int64)
    pair_count = np.zeros(len(PAIRS), dtype=np.int64)
    pair_response = np.zeros((len(PAIRS), 49), dtype=np.int64)
    result = np.empty((4, len(targets), 49), dtype=np.float64)
    markov_ranks = _rank01(markov)
    for start, end in zip(starts, ends):
        tpos = positions[start:end]
        tpos = tpos[tpos >= 0]
        if len(tpos):
            n = max(0, int(start) - 2)
            for ki, strength in enumerate(PRIOR_STRENGTHS):
                correction = np.zeros(49)
                if start >= 2 and n:
                    source = draws[start - 2]
                    aa, bb = np.asarray(list(combinations(source, 2))).T
                    ids = PAIR_INDEX[aa, bb]
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
                        [math.fsum(column) / 15.0 for column in terms.T]
                    )
                    if np.ptp(contrast) > SCORE_VARIANCE_EPSILON:
                        correction = _rank01(contrast) - 0.5
                for ai, amp in enumerate(CONTRAST_AMPLITUDES):
                    result[2 * ki + ai, tpos] = markov_ranks[tpos] + amp * correction
        # Every target of this date becomes available only after all predictions.
        for row in range(max(2, int(start)), int(end)):
            source, response = draws[row - 2], draws[row]
            aa, bb = np.asarray(list(combinations(source, 2))).T
            ids = PAIR_INDEX[aa, bb]
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
    divisor = 96 // (nodes - 1)
    x = np.rint(2.0 * (rankdata(scores1, axis=-1) - 1.0)).astype(np.int64)
    y = np.rint(2.0 * (rankdata(scores2, axis=-1) - 1.0)).astype(np.int64)
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
) -> np.ndarray:
    result = np.empty((8, len(targets), 49), dtype=np.float64)
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
                    posterior = (hits[memory_index] + CALIBRATION_PRIOR * P0) / (
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


def generate(data: dict, expert_scores: dict[str, np.ndarray]) -> Iterator[tuple]:
    """Yield the fixed sixteen scores and exact frequency-fallback audit masks."""
    draws, actual, starts, ends, targets, positions, experts = _validate(
        data, expert_scores
    )
    specs = candidate_specs()
    banks = (
        _bocpd(actual, starts, ends, targets, positions),
        _contrast_predictions(
            draws, actual, starts, ends, targets, positions, experts["markov_lag2"]
        ),
        _calibration_predictions(actual, starts, ends, targets, positions, experts),
    )
    index = 0
    for bank in banks:
        for raw_scores in bank:
            spec = specs[index]
            scores, mask = _finished(raw_scores, experts["frequency"])
            yield spec["name"], spec["family"], scores, mask
            index += 1
