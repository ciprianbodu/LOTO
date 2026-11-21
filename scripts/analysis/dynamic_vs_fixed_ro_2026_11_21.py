"""Compare rolling and fixed method selection on the Romanian lottery archives."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from functools import partial
from itertools import groupby
from multiprocessing import get_context
from pathlib import Path

import numpy as np
from scipy.stats import binomtest

from covering.lr_schemes import lr_schemes
from loto_enterprise.benchmark.decision import holm_adjusted
from scripts.analysis import archived_ro_2026_11_21 as study
from scripts.analysis.compact_ro_2026_11_20 import SETTINGS, archive

# Frozen settings of the v5 selector at the time of this comparison.
WINDOW_DAYS = 104
MIN_DAYS = 26
METHOD_INDEX = {name: index for index, name in enumerate(study.NAMES)}
BANKS = {"active": study.CURRENT, "including_archived": (*study.CURRENT, *sorted(study.RETIRED))}


def rolling_selection(hits, origin_rows, split, candidates, threshold):
    """Choose by completed days, with the v5 tie order and random gate."""
    days = np.arange(len(origin_rows))
    by_day = np.add.reduceat((hits >= threshold).astype(np.int64), origin_rows, axis=0)
    cumulative = np.vstack((np.zeros(hits.shape[1], dtype=np.int64), by_day.cumsum(axis=0)))
    rolling = cumulative[:-1] - cumulative[np.maximum(days - WINDOW_DAYS, 0)]
    best = candidates[np.argmax(rolling[:, candidates], axis=1)]
    beats_random = rolling[days, best] > rolling[:, METHOD_INDEX["random"]]
    chosen = np.where((days >= MIN_DAYS) & beats_random, best, candidates[0])
    return np.repeat(chosen, np.diff(np.r_[origin_rows, len(hits)]))[split:]


def count(values, thresholds):
    return [int((values >= threshold).sum()) for threshold in thresholds]


def evaluate(key, *, cached_scores):
    secondary = key == "joker_urna2"
    lot, draws, dates, cutoffs, origins, universe = archive(key)
    split_day = len(origins) * 7 // 10
    first = int(origins[0])
    split_row = int(origins[split_day]) - first
    target_draws = draws[first:]
    actual = np.zeros((len(target_draws), universe + 1), dtype=bool)
    actual[np.arange(len(target_draws))[:, None], target_draws] = True
    date_offsets = np.searchsorted(origins, cutoffs[first:])
    if cached_scores:
        scores, fallback = study.read_score_cache(
            cached_scores, key, draws, dates, cutoffs, origins, split_day, universe
        )
    else:
        scores, fallback = study.replay_scores(draws, cutoffs, origins, universe)

    threshold, thresholds = (1, (1,)) if secondary else (4, (3, 4, 5, 6))
    if secondary:
        groups = [(1, []), (5, [])]
    else:
        schemes = sorted(lr_schemes(lot.geometry), key=lambda item: (item.n, item.code))
        groups = [(k, list(s)) for k, s in groupby(schemes, key=lambda item: item.n)]
    banks = {
        bank: np.array([
            METHOD_INDEX[name] for name in names
            if not fallback[:split_day, METHOD_INDEX[name]].all()
        ])
        for bank, names in BANKS.items()
    }
    results = []
    for k, schemes in groups:
        pool_hits, matched = study.outcomes(
            scores, actual, date_offsets, k, universe, secondary=secondary
        )
        measurements = [
            ("pool", None, pool_hits),
            *(("best_ticket", s.code, study.best_ticket_hits(s, matched)) for s in schemes),
        ]
        training_pool = (pool_hits[:split_row] >= threshold).sum(axis=0)
        for bank, candidates in banks.items():
            dynamic = rolling_selection(pool_hits, origins - first, split_row, candidates, threshold)
            fixed = max(candidates, key=lambda i: int(training_pool[i]))
            for scope, code, hits in measurements:
                training = (hits[:split_row] >= threshold).sum(axis=0)
                fixed_for_metric = max(candidates, key=lambda i: int(training[i]))
                test = hits[split_row:]
                dynamic_hits = test[np.arange(len(test)), dynamic]
                fixed_hits = test[:, fixed]
                won_dynamic = dynamic_hits >= threshold
                won_fixed = fixed_hits >= threshold
                gained = int((won_dynamic & ~won_fixed).sum())
                lost = int((~won_dynamic & won_fixed).sum())
                results.append({
                    "game": key, "k": k, "code": code, "scope": scope, "bank": bank,
                    "n": len(test), "candidate_count": len(candidates), "thresholds": thresholds,
                    "dynamic": count(dynamic_hits, thresholds),
                    "fixed_pool_choice": count(fixed_hits, thresholds),
                    "fixed_same_metric": count(test[:, fixed_for_metric], thresholds),
                    "fixed_method": study.NAMES[fixed],
                    "fixed_method_same_metric": study.NAMES[fixed_for_metric],
                    "frequency": count(test[:, METHOD_INDEX["frequency"]], thresholds),
                    "random": count(test[:, METHOD_INDEX["random"]], thresholds),
                    "only_dynamic": gained, "only_fixed": lost,
                    "dynamic_method_draws": dict(Counter(study.NAMES[i] for i in dynamic)),
                    "p_two_sided": float(binomtest(gained, gained + lost, 0.5).pvalue) if gained + lost else 1.0,
                })
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--cached-scores", type=Path)
    parser.add_argument("--jobs", choices=range(1, 5), type=int, default=1)
    args = parser.parse_args()
    evaluate_game = partial(evaluate, cached_scores=args.cached_scores)
    if args.jobs == 1:
        games = list(map(evaluate_game, SETTINGS))
    else:
        with ProcessPoolExecutor(max_workers=args.jobs, mp_context=get_context("spawn")) as pool:
            games = list(pool.map(evaluate_game, SETTINGS))
    rows = [row for game in games for row in game]
    for bank in BANKS:
        corrected = holm_adjusted({
            index: row["p_two_sided"]
            for index, row in enumerate(rows) if row["bank"] == bank
        })
        for index, p_value in corrected.items():
            rows[index]["p_holm"] = p_value
    args.output.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
