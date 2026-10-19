"""Generic retrospective day-cluster comparisons and causal selector replay.

No production state or old experimental outcomes are loaded. Day-level paired
label exchangeability is assumed; all histories have been explored previously.
No entire-search IID calibration or prospective proof is claimed. Every pool
column keeps its joint day sign. Per-row McNemar is diagnostic only.
"""

from __future__ import annotations

import sys
from fractions import Fraction
from numbers import Integral
from pathlib import Path

import numpy as np
from scipy.stats import binomtest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from validate_pool_combinations_2026_10_19 import (
    _boundary,
    _dates,
    _fraction,
    _indices,
)
from validate_pool_screen_2026_10_19 import (
    _day_groups,
    _exact_sign_flip,
    holm_adjusted,
    hypergeom_tails,
)


def _meta(ks, draw_n):
    sizes = tuple(ks)
    if (
        not sizes
        or any(
            isinstance(k, (bool, np.bool_)) or not isinstance(k, Integral) or k < 1
            for k in sizes
        )
        or len(set(sizes)) != len(sizes)
    ):
        raise ValueError("ks must contain distinct positive integer pool sizes")
    if (
        isinstance(draw_n, (bool, np.bool_))
        or not isinstance(draw_n, Integral)
        or draw_n < 1
    ):
        raise ValueError("draw_n must be a positive integer")
    return tuple(map(int, sizes)), int(draw_n)


def _validated_hits(values, name, ks, draw_n):
    ks, draw_n = _meta(ks, draw_n)
    arr = np.asarray(values)
    if arr.ndim != 2 or arr.shape[1] != len(ks) or not arr.shape[0]:
        raise ValueError(f"{name} needs nonempty [rows,{len(ks)}] hits")
    numeric = np.asarray(arr, dtype=np.float64)
    if (
        not np.isfinite(numeric).all()
        or not (
            (numeric >= 0) & (numeric <= draw_n) & (numeric == np.floor(numeric))
        ).all()
    ):
        raise ValueError(f"{name} needs integer hits in 0..{draw_n}")
    return numeric.astype(np.int64)


def metrics(hits, *, ks, draw_n, threshold=4):
    values = _validated_hits(hits, "metrics", ks, draw_n)
    if threshold not in range(1, draw_n + 1):
        raise ValueError("invalid event threshold")
    return {
        "n": len(values),
        "primary": float((values >= threshold).mean()),
        "rate3plus": float((values >= 3).mean()),
        "rate5plus": float((values >= 5).mean()),
        "mean_hits": float(values.mean()),
        "zero_rate": float((values == 0).mean()),
        "events4": (values >= 4).sum(axis=0).tolist(),
        "events3": (values >= 3).sum(axis=0).tolist(),
        "events5": (values >= 5).sum(axis=0).tolist(),
        "zero": (values == 0).sum(axis=0).tolist(),
        "mean_hits_by_k": values.mean(axis=0).tolist(),
        "events_by_threshold": {
            str(t): (values >= t).sum(axis=0).tolist() for t in range(1, draw_n + 1)
        },
    }


def paired_test(
    candidate_hits,
    baseline_hits,
    day_ids,
    *,
    ks,
    draw_n,
    threshold: int = 4,
    permutations: int = 99999,
    seed: int = 2026101904,
    bootstrap_samples: int = 2000,
) -> dict:
    """One-sided paired test of the mean success improvement over the supplied pool sizes.

    Positive differences favor the candidate. wins/losses count DAYS, not the
    overlapping pool columns. For the primary test every draw in a day
    receives the same random sign. CI95 resamples complete days and preserves
    weighting by the number of draws when a day has multiple rows. Per-pool
    exact day-sign-flip tests are secondary, with their own Holm correction;
    the row-level McNemar values are diagnostics when dates repeat.
    The exact sign-flip tail is an independent arithmetic diagnostic; the
    predeclared primary p value is the seeded Monte Carlo estimate.
    """
    ks, draw_n = _meta(ks, draw_n)
    candidate = _validated_hits(candidate_hits, "candidate_hits", ks, draw_n)
    baseline = _validated_hits(baseline_hits, "baseline_hits", ks, draw_n)
    if candidate.shape != baseline.shape:
        raise ValueError("candidate and baseline shapes differ")
    if threshold not in range(1, draw_n + 1):
        raise ValueError("threshold must be an integer in 1..draw_n")
    if int(permutations) != permutations or permutations < 1:
        raise ValueError("permutations must be a positive integer")
    if int(bootstrap_samples) != bootstrap_samples or bootstrap_samples < 0:
        raise ValueError("bootstrap_samples must be a nonnegative integer")
    n_rows, n_pools = candidate.shape
    inverse = _day_groups(day_ids, n_rows)
    n_days = int(inverse.max()) + 1
    candidate_events = candidate >= threshold
    baseline_events = baseline >= threshold
    row_differences = (candidate_events.astype(np.int64) - baseline_events).sum(axis=1)
    day_differences = np.zeros(n_days, dtype=np.int64)
    np.add.at(day_differences, inverse, row_differences)
    day_sizes = np.bincount(inverse, minlength=n_days)
    observed = int(day_differences.sum())
    wins = int((day_differences > 0).sum())
    losses = int((day_differences < 0).sum())
    nonzero = day_differences[day_differences != 0]
    rng = np.random.default_rng(seed)
    if len(nonzero):
        exceed = 0
        for start in range(0, permutations, 4096):
            batch = min(4096, permutations - start)
            signs = (
                rng.integers(0, 2, size=(batch, len(nonzero)), dtype=np.int8) * 2 - 1
            )
            simulated = signs @ nonzero
            exceed += int((simulated >= observed).sum())
        p_value = (exceed + 1) / (permutations + 1)
    else:
        exceed = permutations
        p_value = 1.0
    sign_p = (
        float(binomtest(wins, wins + losses, 0.5, alternative="greater").pvalue)
        if wins + losses
        else 1.0
    )
    ci95 = None
    if bootstrap_samples:
        boot_rng = np.random.default_rng(seed + 1)
        bootstrap = []
        for start in range(0, bootstrap_samples, 128):
            size = min(128, bootstrap_samples - start)
            selected = boot_rng.integers(0, n_days, size=(size, n_days))
            numerator = day_differences[selected].sum(axis=1)
            denominator = n_pools * day_sizes[selected].sum(axis=1)
            bootstrap.append(numerator / denominator)
        ci95 = list(map(float, np.quantile(np.concatenate(bootstrap), [0.025, 0.975])))
    per_pool = {}
    for column, k in enumerate(ks):
        cand = candidate_events[:, column]
        base = baseline_events[:, column]
        b = int((cand & ~base).sum())
        c = int((base & ~cand).sum())
        p = (
            float(binomtest(b, b + c, 0.5, alternative="greater").pvalue)
            if b + c
            else 1.0
        )
        signed_rows = cand.astype(np.int64) - base.astype(np.int64)
        signed_days = np.zeros(n_days, dtype=np.int64)
        np.add.at(signed_days, inverse, signed_rows)
        per_pool[f"k{k}"] = {
            "day_signflip_p": _exact_sign_flip(signed_days),
            "candidate_rate": float(cand.mean()),
            "baseline_rate": float(base.mean()),
            "difference": float(cand.mean() - base.mean()),
            "candidate_only": b,
            "baseline_only": c,
            "mcnemar_p": p,
        }
    adjusted = holm_adjusted(
        {k: value["day_signflip_p"] for k, value in per_pool.items()}
    )
    for k, adjusted_p in adjusted.items():
        per_pool[k]["day_signflip_holm_p"] = adjusted_p
    return {
        "per_pool_scope": "day sign-flip tails corrected across requested pools; row-level McNemar p is diagnostic only when dates repeat",
        "rows": n_rows,
        "days": n_days,
        "pools": list(ks),
        "threshold": threshold,
        "candidate_rate": float(candidate_events.mean()),
        "baseline_rate": float(baseline_events.mean()),
        "mean_difference": observed / (n_rows * n_pools),
        "difference_numerator": observed,
        "wins": wins,
        "losses": losses,
        "ties": int((day_differences == 0).sum()),
        "p_value": p_value,
        "exact_sign_flip_p_diagnostic": _exact_sign_flip(day_differences),
        "sign_p": sign_p,
        "permutations": permutations,
        "permutation_exceedances": exceed,
        "seed": seed,
        "ci95_day_cluster_bootstrap": ci95,
        "bootstrap_samples": bootstrap_samples,
        "per_pool": per_pool,
        "assumption": "paired method-label exchangeability under the null; day-cluster swaps",
    }


def replay_selector(
    full_dates,
    target_indices,
    bank_hits,
    candidate_ids,
    *,
    ks,
    draw_n,
    complexity=None,
    eligibility_mask=None,
    ties=None,
    baseline_id: str = "markov_lag2",
    origins=(0.7, 0.8, 0.9),
    lookback=0.2,
    tie_fraction_limit: float = 0.5,
) -> dict:
    """Reconstruct the fixed rolling selector from past observations only.

    bank_hits has shape [candidates, targets, len(ks)] for a SINGLE explicitly chosen
    mode (primary consecutive2; raw uses the SAME selected_ids afterward).
    eligibility_mask[c,t] means that row's original candidate scores were usable
    before any fallback. Optional ties[c,t,k] marks a raw top-K boundary tie.
    An origin excludes challengers with any unusable score in its PAST window
    or a tie fraction >= the predeclared limit at any pool size in that window.

    The baseline is the explicit reference/reserve and remains available; any
    baseline gate issue is exposed in its diagnostics. Choices maximize the
    INTEGER count of 4+ indicators in the common window. Exact ties prefer the
    baseline, then smaller predeclared complexity, then candidate ID. The choice
    stays fixed in the next block. If its future scores use a fallback, the
    supplied bank pipeline's actual hits remain in the trace and the occurrence
    is reported; future validity never changes the earlier choice.

    Original full_dates determine all percentage boundaries, including repeated
    same-day draws. All rows of every selection/evaluation window must be present
    in target_indices; a partial cached window raises instead of biasing ranking.
    Arrays returned outside the active blocks use the baseline, with active_mask
    making that convention explicit. Inputs are never mutated.
    """
    ks, draw_n = _meta(ks, draw_n)
    dates = _dates(full_dates, "full_dates")
    targets = _indices(target_indices, len(dates))
    ids = tuple(map(str, candidate_ids))
    if not ids or len(ids) != len(set(ids)) or baseline_id not in ids:
        raise ValueError("candidate IDs must be unique and include the baseline")
    hits = np.asarray(bank_hits)
    shape = (len(ids), len(targets), len(ks))
    if hits.shape != shape:
        raise ValueError(f"bank_hits must have shape {shape}")
    numeric = np.asarray(hits, dtype=np.float64)
    if (
        not np.isfinite(numeric).all()
        or not (
            (numeric >= 0) & (numeric <= draw_n) & (numeric == np.floor(numeric))
        ).all()
    ):
        raise ValueError(
            "bank_hits must contain finite integer hit counts in 0..draw_n"
        )
    hits = numeric.astype(np.int8)
    if complexity is None:
        complexity_values = np.ones(len(ids))
    else:
        complexity_values = np.asarray(complexity, dtype=np.float64)
        if (
            complexity_values.shape != (len(ids),)
            or not (np.isfinite(complexity_values) & (complexity_values >= 0)).all()
        ):
            raise ValueError("complexity must be a finite nonnegative candidate vector")
    valid = np.ones(shape[:2], dtype=bool)
    if eligibility_mask is not None:
        supplied = np.asarray(eligibility_mask)
        if supplied.shape != shape[:2] or not ((supplied == 0) | (supplied == 1)).all():
            raise ValueError(
                "eligibility_mask must be a boolean [candidates, targets] matrix"
            )
        valid = supplied.astype(bool)
    tied = np.zeros(shape, dtype=bool)
    if ties is not None:
        supplied = np.asarray(ties)
        if supplied.shape != shape or not ((supplied == 0) | (supplied == 1)).all():
            raise ValueError(
                "ties must be a boolean [candidates, targets, len(ks)] array"
            )
        tied = supplied.astype(bool)
    if not np.isfinite(tie_fraction_limit) or not 0 < tie_fraction_limit <= 1:
        raise ValueError("tie_fraction_limit must be in (0,1]")
    origin_values = tuple(_fraction(value, "origin") for value in origins)
    lookback_value = _fraction(lookback, "lookback")
    if not origin_values or tuple(sorted(set(origin_values))) != origin_values:
        raise ValueError("origins must be strictly increasing")
    if any(origin <= lookback_value for origin in origin_values):
        raise ValueError("every origin must have a complete positive past window")
    unique, full_day = np.unique(dates, return_inverse=True)
    target_day = full_day[targets]
    baseline = ids.index(baseline_id)
    selected = np.full(len(targets), baseline, dtype=np.int64)
    trace = hits[baseline].copy()
    active = np.zeros(len(targets), dtype=bool)
    blocks = []
    stops = origin_values[1:] + (Fraction(1),)
    for origin, stop in zip(origin_values, stops, strict=True):
        lo = _boundary(len(unique), origin - lookback_value)
        cutoff = _boundary(len(unique), origin)
        end = _boundary(len(unique), stop)
        if not 0 <= lo < cutoff < end <= len(unique):
            raise ValueError("too few original dates for the requested split")
        window = (target_day >= lo) & (target_day < cutoff)
        evaluation = (target_day >= cutoff) & (target_day < end)
        expected_window = np.flatnonzero((full_day >= lo) & (full_day < cutoff))
        expected_evaluation = np.flatnonzero((full_day >= cutoff) & (full_day < end))
        if not np.array_equal(targets[window], expected_window):
            raise ValueError("target_indices omit part of a past selection window")
        if not np.array_equal(targets[evaluation], expected_evaluation):
            raise ValueError("target_indices omit part of an evaluation block")
        if np.any(active & evaluation):
            raise ValueError("evaluation blocks overlap")
        event_counts = np.count_nonzero(hits[:, window] >= 4, axis=(1, 2))
        invalid_rows = np.count_nonzero(~valid[:, window], axis=1)
        tie_rates = tied[:, window].mean(axis=1)
        eligible = (invalid_rows == 0) & ~(tie_rates >= tie_fraction_limit).any(axis=1)
        original_baseline_gate = bool(eligible[baseline])
        eligible[baseline] = True
        available = np.flatnonzero(eligible)
        chosen = min(
            available,
            key=lambda index: (
                -int(event_counts[index]),
                0 if index == baseline else 1,
                float(complexity_values[index]),
                ids[index],
            ),
        )
        selected[evaluation] = chosen
        trace[evaluation] = hits[chosen, evaluation]
        active |= evaluation
        blocks.append(
            {
                "origin": float(origin),
                "stop": float(stop),
                "window_day_indices": [lo, cutoff],
                "evaluation_day_indices": [cutoff, end],
                "window_first": str(unique[lo]),
                "window_last": str(unique[cutoff - 1]),
                "evaluation_first": str(unique[cutoff]),
                "evaluation_last": str(unique[end - 1]),
                "window_rows": int(window.sum()),
                "evaluation_rows": int(evaluation.sum()),
                "chosen_id": ids[chosen],
                "chosen_index": int(chosen),
                "chosen_past_primary": int(event_counts[chosen])
                / (int(window.sum()) * len(ks)),
                "baseline_past_primary": int(event_counts[baseline])
                / (int(window.sum()) * len(ks)),
                "selected_future_invalid_rows": int(
                    np.count_nonzero(~valid[chosen, evaluation])
                ),
                "baseline_reference_override": not original_baseline_gate,
                "gates": {
                    name: {
                        "eligible": bool(eligible[index]),
                        "invalid_rows_in_past_window": int(invalid_rows[index]),
                        "tie_fractions_in_past_window": tie_rates[index].tolist(),
                        "past_4plus_count": int(event_counts[index]),
                        "complexity": float(complexity_values[index]),
                    }
                    for index, name in enumerate(ids)
                },
            }
        )
    primary_start = _boundary(len(unique), Fraction(4, 5))
    diagnostic_start = _boundary(len(unique), Fraction(7, 10))
    primary = active & (target_day >= primary_start)
    diagnostic = (
        active & (target_day >= diagnostic_start) & (target_day < primary_start)
    )
    return {
        "trace_hits": trace,
        "selected_indices": selected,
        "selected_ids": np.asarray(ids)[selected],
        "active_mask": active,
        "primary_mask": primary,
        "diagnostic_mask": diagnostic,
        "target_dates": dates[targets].astype(str),
        "blocks": blocks,
        "baseline_id": baseline_id,
        "pools": list(ks),
        "retrospective_reuse": True,
        "iid_search_calibrated": False,
    }
