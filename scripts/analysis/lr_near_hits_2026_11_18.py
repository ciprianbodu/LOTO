"""Compare chronological scorer selection on the actual rows of a reduced lottery scheme.

For each of 13 main urns, compare four policies on the final 30% of eligible draw
dates. Use the nine scorers, source histories, default schemes and date boundaries
of nested_wfv_2026_11_18. Select a scorer every 26 draw dates using only earlier
observations. All policies use the same game, tickets and price at each origin.

Primary outcome: at least one actual ticket with 5+ matches. Frequency and direct
selection by past 5+, then 4+, then mean best-ticket hits are the fixed comparators.
Two candidates: (1) select by 4+, then 3+, then mean best-ticket hits; (2) select
by coverage of the closed one-substitution neighborhoods of all past draws. Count
each neighbor once if any ticket has 5+. Only actual draws enter the test totals.
Ties use the declared method order. At Joker Urna 2 the closed neighborhood covers
the entire urn, so this selection criterion is constant and the urn is omitted.

Primary paired e-value threshold under independent uniform draws: 2 methods x 13
games x 2 comparators / 0.05 = 1040. Other thresholds are descriptive. The
previously used source archives make all comparisons exploratory.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
from hashlib import sha256
import json
from pathlib import Path

import numpy as np

from loto_enterprise.core.pool_selection import select_pool_from_scores
from scripts.analysis import nested_wfv_2026_11_18 as wf


CANDIDATES = ("surrogate43", "near5")
CONTROLS = ("frequency", "direct5")


def radius_one_counts(universe, draw_size, pool_size, best_hits, threshold=5):
    """For each observed pool mask, count distinct successes within one substitution."""
    succeeds = np.asarray(best_hits) >= threshold
    result = np.zeros(1 << pool_size, dtype=np.int64)
    for mask in range(1 << pool_size):
        hits_in_pool = mask.bit_count()
        outside_drawn = draw_size - hits_in_pool
        outside_available = universe - pool_size - outside_drawn
        if outside_drawn < 0 or outside_available < 0:
            continue
        present = [1 << bit for bit in range(pool_size) if mask >> bit & 1]
        absent = [1 << bit for bit in range(pool_size) if not mask >> bit & 1]
        # The observed draw and exchanges of two outside numbers keep this mask.
        count = int(succeeds[mask]) * (1 + outside_drawn * outside_available)
        for added in absent:
            count += outside_drawn * int(succeeds[mask | added])
        for removed in present:
            without = mask ^ removed
            count += outside_available * int(succeeds[without])
            for added in absent:
                count += int(succeeds[without | added])
        result[mask] = count
    return result


def historical_masks(matrix, cutoffs, days, universe, pool_size):
    """Predict once per date, and keep the actual sorted-pool positions hit on each draw."""
    first = days[0]
    masks = np.empty((len(matrix) - first, len(wf.METHODS)), dtype=np.uint32)
    for start, stop in zip(days, np.r_[days[1:], len(matrix)], strict=True):
        for index, method in enumerate(wf.METHODS):
            scores, _ = wf.call_method(method, matrix[:start], universe, history_cutoffs=cutoffs[:start])
            if len(scores) != universe or not wf.has_usable_score_variance(scores):
                raise ValueError(f"Invalid scores for {method} at history row {start}")
            numbers = select_pool_from_scores(scores, pool_size, set(), max_num=universe, max_consecutive_run=2)
            if len(numbers) != pool_size or numbers != sorted(numbers):
                raise ValueError(f"Invalid positional pool for {method} at history row {start}")
            bits = np.zeros(universe + 1, dtype=np.uint32)
            bits[numbers] = 1 << np.arange(pool_size)
            masks[start - first:stop - first, index] = bits[matrix[start:stop]].sum(axis=1)
    return masks


def selected_methods(tickets, neighborhood_scores, ends, split):
    """Select with cumulative values up to, and excluding, the first row of each block."""
    sums = {threshold: wf.cumulative(tickets >= threshold) for threshold in (3, 4, 5)}
    sums["hits"] = wf.cumulative(tickets)
    sums["near"] = wf.cumulative(neighborhood_scores)
    policies = {"direct5": (5, 4, "hits"), "surrogate43": (4, 3, "hits"), "near5": ("near",)}
    choices = {"frequency": np.zeros(len(tickets) - ends[split], dtype=np.int16)}
    for policy, criteria in policies.items():
        def choose(day):
            return max(range(tickets.shape[1]),
                       key=lambda col: tuple(sums[field][ends[day], col] for field in criteria))

        choices[policy], _ = wf.forecast_methods(ends, split, wf.BLOCK_DAYS, choose)
    return choices


def study_game(key):
    game = next(game for game in wf.GAMES if key == game.bench_key)
    pool_size, scheme, best = wf.scheme_definition(game, False, None)
    matrix, dates, cutoffs, days, universe, digest = wf.read_draws(game, False)
    best_hits = np.array([best(mask) for mask in range(1 << pool_size)], dtype=np.uint8)
    neighborhood = radius_one_counts(universe, game.draw_n, pool_size, best_hits)
    masks = historical_masks(matrix, cutoffs, days, universe, pool_size)
    tickets = best_hits[masks]
    ends = np.r_[days - days[0], len(tickets)]
    split = wf.MIN_VALIDATION_DAYS + (len(days) - wf.MIN_VALIDATION_DAYS) * 7 // 10
    choices = selected_methods(tickets, neighborhood[masks], ends, split)

    rows = np.arange(ends[split], len(tickets))
    ticket_results = {name: tickets[rows, columns] for name, columns in choices.items()}
    arms = {}
    for name, values in ticket_results.items():
        columns = choices[name]
        pool_hits = np.bitwise_count(masks[rows, columns])
        events = []
        for index in np.flatnonzero(values >= 5):
            source_row = int(days[0] + rows[index])
            events.append({"date": str(dates[source_row]), "draw_index": source_row,
                           "pool_hits": int(pool_hits[index]), "ticket_hits": int(values[index]),
                           "method": wf.METHODS[columns[index]]})
        summary = {
            **{str(t): int((values >= t).sum()) for t in (3, 4, 5, 6)},
            "pool5": int((pool_hits >= 5).sum()),
            "five_plus_draws": events,
            "draws_by_method": dict(zip(wf.METHODS, map(int, np.bincount(columns, minlength=len(wf.METHODS))), strict=True)),
        }
        if name in CANDIDATES:
            summary["paired"] = {
                control: {str(t): wf.paired(values >= t, ticket_results[control] >= t) for t in (3, 4, 5)}
                for control in CONTROLS
            }
        arms[name] = summary

    return {"key": key, "n": len(rows), "n_dates": len(days) - split,
            "start": str(dates[days[split]]), "end": str(dates[-1]),
            "draw_size": game.draw_n, "universe": universe, "pool_size": pool_size,
            "scheme": scheme, "csv_sha256": digest, "arms": arms}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--protocol-only", action="store_true")
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be a positive integer")
    keys = [game.bench_key for game in wf.GAMES]
    result = {"protocol": __doc__, "methods": wf.METHODS, "games_studied": keys,
              "block_dates": wf.BLOCK_DAYS, "primary_e_threshold": 2 * len(keys) * 2 / 0.05,
              "script_sha256": sha256(Path(__file__).read_bytes()).hexdigest()}
    if not args.protocol_only:
        if args.workers == 1:
            result["games"] = list(map(study_game, keys))
        else:
            with ProcessPoolExecutor(max_workers=args.workers) as pool:
                result["games"] = list(pool.map(study_game, keys))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
