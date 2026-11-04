"""Exploratory prior-only pool-hit screen; never writes production decisions.

The grid below is fixed in source before reading results. All archived data have
already been used in earlier research: chronological splits are NOT fresh
holdouts. Structural/pseudomathematical scorers are negative-control hypotheses,
not production candidates. Hits refer to the pool, never to a ticket or prize.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import beta, binomtest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from loto_enterprise.core.draw_validation import valid_draw_matrix
from loto_enterprise.core.ranking import rank_by_score

WARMUP = 250
WINDOWS = (10, 25, 50, 100, 250, 500)
COLD_WINDOWS = (25, 100, 250)
HALF_LIVES = (5, 10, 25, 50, 100, 250)
METHODS = (
    "frequency",
    *(f"frequency_{w}" for w in WINDOWS),
    *(f"cold_{w}" for w in COLD_WINDOWS),
    *(f"ewma_{h}" for h in HALF_LIVES),
    "gap_recent", "gap_overdue", "repeat_previous", "avoid_previous",
    "neighbor_1", "neighbor_2", "even", "odd", "prime", "composite",
    "low", "high", "digit_0", "digit_5", "previous_sum_center",
)
GAMES = {
    "loto_6_49": (49, 6, (6, 12, 16), (4, 5, 6)),
    "loto_5_40": (40, 6, (5, 12, 19), (4, 5, 6)),
    "joker": (45, 5, (5, 12, 15), (4, 5)),
}
STRUCTURAL = set(METHODS[20:])


def hypergeom_tail(universe, draw_n, pool_size, threshold):
    """Exact integer favourable count divided once; includes impossible events."""
    if not 0 <= pool_size <= universe or not 0 <= draw_n <= universe:
        raise ValueError("invalid geometry")
    low = max(0, draw_n - universe + pool_size, threshold)
    high = min(pool_size, draw_n)
    return sum(
        math.comb(pool_size, h) * math.comb(universe - pool_size, draw_n - h)
        for h in range(low, high + 1)
    ) / math.comb(universe, draw_n)


def holm(p_values):
    """Holm adjusted p-values in the original order, for one entire family."""
    values = np.asarray(p_values, dtype=float)
    if not np.all(np.isfinite(values)) or np.any((values < 0) | (values > 1)):
        raise ValueError("p-values must lie in [0, 1]")
    order = np.argsort(values, kind="stable")
    out = np.empty(len(values), dtype=float)
    running = 0.0
    for rank, index in enumerate(order):
        running = max(running, (len(values) - rank) * values[index])
        out[index] = min(1.0, running)
    return out.tolist()


def confidence_interval(successes, total, alpha=0.05):
    """Clopper-Pearson marginal interval (not simultaneous or selection-adjusted)."""
    if total < 1 or not 0 <= successes <= total:
        raise ValueError("invalid binomial count")
    lo = 0.0 if successes == 0 else float(beta.ppf(alpha / 2, successes, total - successes + 1))
    hi = (
        1.0
        if successes == total
        else float(beta.ppf(1 - alpha / 2, successes + 1, total - successes))
    )
    return [lo, hi]


def paired_pvalue(candidate, baseline):
    wins = int(np.sum(candidate & ~baseline))
    losses = int(np.sum(baseline & ~candidate))
    discordant = wins + losses
    return (
        wins,
        losses,
        (
            float(binomtest(wins, discordant, 0.5, alternative="greater").pvalue)
            if discordant
            else 1.0
        ),
    )


def load_game(root, game, universe, draw_n):
    path = Path(root) / "_ISTORIC" / f"{game}.csv"
    frame = pd.read_csv(path)
    dates = pd.to_datetime(frame["date"], format="%d-%m-%Y", errors="coerce")
    (matrix, accepted) = valid_draw_matrix(
        frame,
        [f'n{i}' for i in range(1, draw_n + 1)],
        draw_n=draw_n,
        max_num=universe,
    )
    if not bool(np.all(accepted)) or dates.isna().any():
        raise ValueError(f"{path}: invalid history rows; refusing partial screen")
    order = np.argsort(dates.to_numpy(), kind="stable")
    return matrix[order], dates.to_numpy()[order], hashlib.sha256(path.read_bytes()).hexdigest()


def score_grid(indicators, prefix, index, universe, ewma, last_seen):
    """Scores use rows strictly before index; caller excludes every target day."""
    numbers = np.arange(1, universe + 1)
    counts = prefix[index]
    last = indicators[index - 1] if index else np.zeros(universe)
    prime = np.array(
        [n >= 2 and all((n % d for d in range(2, math.isqrt(int(n)) + 1))) for n in numbers],
        dtype=float,
    )
    vectors = [counts]
    vectors.extend(counts - prefix[max(0, index - w)] for w in WINDOWS)
    vectors.extend(-(counts - prefix[max(0, index - w)]) for w in COLD_WINDOWS)
    vectors.extend(ewma)
    gaps = index - last_seen
    vectors.extend((-gaps, gaps, last, -last))
    previous = numbers[last.astype(bool)]
    for distance in (1, 2):
        vectors.append(
            np.sum(
                (
                    (np.abs(numbers[:, None] - previous[None, :]) >= 1) & (np.abs(numbers[:, None] - previous[None, :]) <= distance)
                ),
                axis=1,
            ),
        )
    center = float(np.mean(previous)) if len(previous) else (universe + 1) / 2
    vectors.extend(
        (
            numbers % 2 == 0,
            numbers % 2 == 1,
            prime,
            1 - prime - (numbers == 1),
            -numbers,
            numbers,
            numbers % 10 == 0,
            numbers % 10 == 5,
            -np.abs(numbers - center),
        ),
    )
    assert len(vectors) == len(METHODS)
    return vectors


def walk_forward_hits(matrix, dates, universe, pools, warmup=WARMUP):
    """One canonical rank per method/day; no per-pool redundant sorting."""
    matrix = np.asarray(matrix, dtype=int)
    dates = np.asarray(dates)
    if len(matrix) != len(dates) or np.any(dates[1:] < dates[:-1]):
        raise ValueError("history must be date-sorted")
    indicators = np.zeros((len(matrix), universe), dtype=np.int64)
    indicators[np.arange(len(matrix))[:, None], matrix - 1] = 1
    prefix = np.vstack((np.zeros(universe, dtype=np.int64), np.cumsum(indicators, axis=0)))
    decay = np.power(0.5, 1 / np.array(HALF_LIVES, dtype=float))[:, None]
    ewma = np.zeros((len(HALF_LIVES), universe), dtype=float)
    last_seen = np.full(universe, -1, dtype=np.int64)
    result = []
    evaluation_indices = []
    start = 0
    while start < len(matrix):
        stop = start + 1
        while stop < len(matrix) and dates[stop] == dates[start]:
            stop += 1
        if start >= warmup:
            vectors = score_grid(indicators, prefix, start, universe, ewma, last_seen)
            ranks = [
                rank_by_score(
                    dict(enumerate(map(float, vector), start=1)), max(pools)
                )
                for vector in vectors
            ]
            for row in range(start, stop):
                actual = set(map(int, matrix[row]))
                result.append(
                    [[sum((n in actual for n in rank[:k])) for k in pools] for rank in ranks],
                )
                evaluation_indices.append(row)
        # All rows of this day become visible together only after scoring it.
        for row in range(start, stop):
            ewma = decay * ewma + indicators[row]
            last_seen[matrix[row] - 1] = row
        start = stop
    return np.asarray(result, dtype=np.uint8), np.asarray(evaluation_indices, dtype=int)


def analyze(root=ROOT):
    started = time.perf_counter()
    rows, sources = [], {}
    for game, (universe, draw_n, pools, thresholds) in GAMES.items():
        matrix, dates, digest = load_game(root, game, universe, draw_n)
        hits, indices = walk_forward_hits(matrix, dates, universe, pools)
        # Split only between days. Both periods are reused retrospective data.
        cut = int(len(indices) * 0.7)
        while cut < len(indices) and cut > 0 and dates[indices[cut]] == dates[indices[cut - 1]]:
            cut += 1
        sources[game] = {
            "sha256": digest,
            "draws": len(matrix),
            "evaluation_draws": len(indices),
            "first_evaluated_date": str(dates[indices[0]])[:10],
            "last_evaluated_date": str(dates[indices[-1]])[:10],
            "period_sizes": [cut, len(indices) - cut],
            "draw_n": draw_n,
            "pool_sizes": pools,
        }
        for period, selected in (
            ("earlier_70pct", slice(None, cut)),
            ("recent_30pct", slice(cut, None)),
        ):
            period_hits = hits[selected]
            for m, method in enumerate(METHODS):
                for k_index, k in enumerate(pools):
                    for threshold in thresholds:
                        event = period_hits[:, m, k_index] >= threshold
                        reference = period_hits[:, 0, k_index] >= threshold
                        n, successes = len(event), int(np.sum(event))
                        p0 = hypergeom_tail(universe, draw_n, k, threshold)
                        wins, losses, p_pair = paired_pvalue(event, reference)
                        rows.append(
                            {
                                "game": game,
                                "method": method,
                                "structural_control": method in STRUCTURAL,
                                "period": period,
                                "pool": k,
                                "threshold": threshold,
                                "n": n,
                                "successes": successes,
                                "rate": successes / n,
                                "baseline_rate": p0,
                                "expected_successes": n * p0,
                                "relative_lift": successes / n / p0 - 1 if p0 else None,
                                "interval_95pct_marginal": confidence_interval(successes, n),
                                "paired_wins_vs_frequency": wins,
                                "paired_losses_vs_frequency": losses,
                                "p_binomial": float(
                                    binomtest(successes, n, p0, alternative="greater").pvalue,
                                ),
                                "p_paired": p_pair,
                            },
                        )
        print(
            f'{game}: {len(indices)} prior-only predictions x {len(METHODS)} methods',
            flush=True,
        )
    p_values = [r[key] for r in rows for key in ("p_binomial", "p_paired")]
    adjusted = holm(p_values)
    for i, row in enumerate(rows):
        row["p_binomial_holm"] = adjusted[2 * i]
        row["p_paired_holm"] = adjusted[2 * i + 1]
    qualified = [
        r for r in rows
        if r["period"] == "recent_30pct"
        and r["p_binomial_holm"] < 0.05
        and r["p_paired_holm"] < 0.05
    ]
    return {
        "protocol": {
            "status": "exploratory_reused_archive_no_fresh_holdout",
            "warmup_draws": WARMUP,
            "methods": list(METHODS),
            "method_count": len(METHODS),
            "multiple_testing_family": "all games, methods, pools, thresholds, both periods, both tests",
            "tests": len(p_values),
            "cells": len(rows),
            "null": (
                "uniform independent draws; fixed pool size conditional on all "
                "prior information"
            ),
            "intervals": (
                "95% marginal Clopper-Pearson, not simultaneous; displayed winners "
                "are selection-biased"
            ),
            "paired_test": (
                "one-sided exact discordant-pair sign test versus frequency; "
                "exploratory diagnostic under exchangeable discordances"
            ),
            "same_day": "every draw on the target date is excluded from score history",
            "game_540": (
                "six main balls for pool hits; five on a ticket; pool 6-hit does "
                "not mean a six-hit ticket"
            ),
            "joker": "main urn only; Joker-ball prizes are not modeled",
            "production_promotions": 0,
            "jackpot_649": (
                "For B distinct six-number tickets, P(at least one exact "
                "6)=B/C(49,6) under uniform draws; reordering cannot raise it at "
                "fixed B."
            ),
            "complete_system": (
                "Enumerating every pick-sized subset of a fixed pool attains "
                "best_ticket_hits=min(pool_hits,pick); no wheel on that pool can "
                "exceed this bound."
            ),
            "sources": [
                (
                    "https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.hypergeom.html"
                ),
                (
                    "https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.binomtest.html"
                ),
            ],
            "pseudomathematical_controls": (
                "numeric labels, parity, prime/composite, last-digit and sum-center"
                " controls remain offline"
            ),
        },
        "sources": sources,
        "summary": {
            "recent_cells_passing_both_holm_tests": len(qualified),
            "min_binomial_holm": min((r["p_binomial_holm"] for r in rows)),
            "min_paired_holm": min((r["p_paired_holm"] for r in rows)),
            "unadjusted_binomial_below_005": sum((r["p_binomial"] < 0.05 for r in rows)),
            "unadjusted_paired_below_005": sum((r["p_paired"] < 0.05 for r in rows)),
        },
        "seconds": time.perf_counter() - started,
        "rows": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).with_suffix(".json"))
    args = parser.parse_args()
    report = analyze()
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"{report["protocol"]["tests"]} tests; {report["seconds"]:.2f} seconds; {args.output}")


if __name__ == "__main__":
    main()
