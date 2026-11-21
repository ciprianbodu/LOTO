"""Replay the archived LOTO methods on the three Romanian lottery games."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from concurrent.futures import ProcessPoolExecutor
from functools import partial
from itertools import groupby
from multiprocessing import get_context
from pathlib import Path

import numpy as np
from scipy.stats import binom

from covering.lr_schemes import lr_schemes, scheme_odds
from covering.probability import subset_hit_table
from loto_enterprise.benchmark import methods
from loto_enterprise.benchmark.decision import expected_random_rate, holm_adjusted
from loto_enterprise.core.dynamic_pools import ranked_pools
from loto_enterprise.core.score_validation import has_usable_score_variance
from scripts.analysis import legacy_methods_2026_11_20 as legacy
from scripts.analysis.compact_ro_2026_11_20 import AS_OF, SETTINGS, STUDY_METHODS, archive


REFERENCES = ("frequency", "random")
ARCHIVED = tuple(name for name in legacy.METHODS if name not in REFERENCES)
RETIRED = tuple(name for name in ARCHIVED if name != "markov_lag2")
CURRENT = ("frequency", *STUDY_METHODS, "markov_lag2")
NAMES = (*REFERENCES, *ARCHIVED, *STUDY_METHODS)
REFERENCE_POOLS = {"loto_6_49": 16, "loto_5_40": 19, "joker_urna1": 15, "joker_urna2": 1}


def replay_scores(draws, cutoffs, origins, universe):
    """Compute forecasts strictly before each target date."""
    scores = np.empty((len(origins), len(NAMES), universe))
    fallback = np.zeros(scores.shape[:2], dtype=bool)
    for index, origin in enumerate(origins):
        past, bounds = draws[:origin], cutoffs[:origin]
        for arm, name in enumerate(NAMES):
            caller = methods.call_method if name in REFERENCES + STUDY_METHODS else legacy.call_method
            forecast, _ = caller(name, past, universe, history_cutoffs=bounds)
            row = np.array([forecast[ball] for ball in range(1, universe + 1)])
            valid = len(forecast) == universe and np.isfinite(row).all() and (row >= 0).all()
            if valid and (name == "frequency" or has_usable_score_variance(forecast)):
                scores[index, arm] = row
            elif name in REFERENCES:
                raise ValueError(f"Unusable reference {name} at origin {origin}")
            else:
                fallback[index, arm] = True
                scores[index, arm] = scores[index, 0]
    return scores, fallback


def read_score_cache(cache_dir, key, draws, dates, cutoffs, origins, split, universe):
    """Load independently evaluated scores only when they refer to the same data."""
    path = cache_dir / f"{key}.npz"
    with np.load(path, allow_pickle=False) as source:
        checks = {"draws": draws, "dates": dates, "cutoffs": cutoffs, "origins": origins,
                  "split_day": split}
        for name, expected in checks.items():
            if not np.array_equal(source[name], expected):
                raise ValueError(f"{path}: mismatched {name}")
        names = source["names"].tolist()
        if len(names) != len(NAMES) or set(names) != set(NAMES):
            raise ValueError(f"{path}: mismatched methods")
        indices = [names.index(name) for name in NAMES]
        scores = source["scores"][:, indices]
        fallback = source["fallback"][:, indices]
        if (
            scores.shape != (len(origins), len(NAMES), universe)
            or fallback.shape != scores.shape[:2]
            or not np.isfinite(scores).all()
            or (scores < 0).any()
            or fallback[:, :2].any()
        ):
            raise ValueError(f"{path}: invalid scores or availability")
    return scores, fallback


def outcomes(scores, actual, date_offsets, pool_size, universe, *, secondary):
    pools = ranked_pools(
        scores.reshape(-1, universe),
        pool_size=pool_size,
        universe=universe,
        max_run=0 if secondary else 2,
    ).reshape(len(scores), len(NAMES), pool_size)
    matching = actual[np.arange(len(actual))[:, None, None], pools[date_offsets]]
    return matching.sum(axis=2, dtype=np.uint8), matching


def best_ticket_hits(scheme, matched):
    masks, _ = scheme._position_data
    best = subset_hit_table(scheme.n, masks)[1]
    places = 1 << np.arange(scheme.n, dtype=np.int32)
    return best[matched @ places]


def summarize(hits, thresholds, cut, fallback, odds, context):
    primary = 1 if 1 in thresholds else 4
    tested = hits[cut:]
    draws = len(tested)
    selection_targets = (primary,) if primary == 1 else (primary, 3)
    train_hits = {n: (hits[:cut] >= n).sum(axis=0) for n in selection_targets}
    counts = {n: (tested >= n).sum(axis=0) for n in thresholds}
    eligible = [name for index, name in enumerate(NAMES) if not fallback["train"][index]]
    chosen = []
    for target in selection_targets:
        for arm, bank in (("archive_locked", RETIRED), ("current_locked", CURRENT)):
            name = min(
                (name for name in bank if name in eligible),
                key=lambda name: (-int(train_hits[target][NAMES.index(name)]),
                                  name != "frequency", name),
            )
            chosen.append((arm if target == primary else arm + "_3plus", name, target))
    rows = []
    for arm, name, target in [*((name, name, primary) for name in NAMES), *chosen]:
        index = NAMES.index(name)
        valid_test_days = context["test_dates"] - int(fallback["test"][index])
        successes = tested[:, index] >= target
        reference = tested[:, 1] >= target
        own = valid_test_days > 0
        complete = valid_test_days == context["test_dates"]
        assessed = selection_targets if arm == name else (target,)
        row = {
            **context,
            "arm": arm,
            "method": name,
            "catalog": (
                "control" if name in REFERENCES else "reactivated" if name == "markov_lag2"
                else "archive" if name in RETIRED else "compact"
            ),
            "selection_and_pairing_threshold": target,
            "train_hits_at_selection_threshold": int(train_hits[target][index]),
            "hits_1": int(counts[1][index]) if own and 1 in counts else None,
            "hits_3plus": int(counts[3][index]) if own and 3 in counts else None,
            "hits_4plus": int(counts[4][index]) if own and 4 in counts else None,
            "hits_5plus": int(counts[5][index]) if own and 5 in counts else None,
            "hits_6": int(counts[6][index]) if own and 6 in counts else None,
            "expected_random_at_threshold": draws * odds[target],
            "random_empirical_at_threshold": int(counts[target][1]),
            "frequency_at_threshold": int(counts[target][0]),
            "markov_at_threshold": int(counts[target][NAMES.index("markov_lag2")]),
            "only_method_over_random": int((successes & ~reference).sum()) if own else None,
            "only_random_over_method": int((~successes & reference).sum()) if own else None,
            "frequency_fallback_train_dates": int(fallback["train"][index]),
            "frequency_fallback_test_dates": int(fallback["test"][index]),
            "availability": (
                "no_own_forecast" if valid_test_days == 0
                else "partial" if valid_test_days < context["test_dates"]
                else "complete"
            ),
            "p_uniform_primary": (
                float(binom.sf(int(counts[primary][index]) - 1, draws, odds[primary]))
                if complete and primary in assessed else None
            ),
            "p_uniform_3plus": (
                float(binom.sf(int(counts[3][index]) - 1, draws, odds[3]))
                if complete and 3 in assessed else None
            ),
            "p_uniform_5plus": (
                float(binom.sf(int(counts[5][index]) - 1, draws, odds[5]))
                if complete and arm == name and 5 in counts else None
            ),
            "p_uniform_6": (
                float(binom.sf(int(counts[6][index]) - 1, draws, odds[6]))
                if complete and arm == name and 6 in counts else None
            ),
            "p_holm_primary": None,
            "p_holm_3plus": None,
            "p_holm_5plus": None,
            "p_holm_6": None,
        }
        rows.append(row)
    return rows


def evaluate(key, *, cached_scores):
    lot, draws, dates, cutoffs, origins, universe = archive(key)
    split = len(origins) * 7 // 10
    cut = int(origins[split] - origins[0])
    secondary = key == "joker_urna2"
    if cached_scores:
        scores, missing = read_score_cache(cached_scores, key, draws, dates, cutoffs, origins, split, universe)
    else:
        scores, missing = replay_scores(draws, cutoffs, origins, universe)
    fallback = {"train": missing[:split].sum(axis=0), "test": missing[split:].sum(axis=0)}
    actual_draws = draws[origins[0]:]
    actual = np.zeros((len(actual_draws), universe + 1), dtype=bool)
    actual[np.arange(len(actual))[:, None], actual_draws] = True
    date_offsets = np.searchsorted(origins, cutoffs[origins[0]:])
    draw_n = draws.shape[1]
    thresholds = (1,) if secondary else tuple(range(3, draw_n + 1))
    context = {
        "game": key,
        "initial_draws": int(origins[0]),
        "train_draws": cut,
        "train_end": str(dates[origins[split] - 1]),
        "test_draws": len(actual) - cut,
        "test_dates": len(origins) - split,
        "test_start": str(dates[origins[split]]),
        "test_end": str(dates[-1]),
    }
    if secondary:
        groups = [(k, []) for k in (1, 5)]
    else:
        schemes = sorted(lr_schemes(lot.geometry), key=lambda item: (item.n, item.code))
        groups = [(k, list(items)) for k, items in groupby(schemes, key=lambda item: item.n)]
    rows = []
    for k, schemes in groups:
        pool, matched = outcomes(scores, actual, date_offsets, k, universe, secondary=secondary)
        pool_odds = {n: expected_random_rate(universe, draw_n, k, n) for n in thresholds}
        rows.extend(summarize(
            pool, thresholds, cut, fallback, pool_odds,
            {**context, "pool_size": k, "scope": "pool", "lr_code": None, "variants": None},
        ))
        for scheme in schemes:
            ticket = best_ticket_hits(scheme, matched)
            odds = scheme_odds(scheme)
            # At 5/40 there are six drawn balls and only five places on a ticket.
            possible = tuple(n for n in thresholds if n <= lot.pick_n)
            rows.extend(summarize(
                ticket, possible, cut, fallback, odds,
                {**context, "pool_size": k, "scope": "best_ticket",
                 "lr_code": scheme.code, "variants": scheme.size},
            ))
    source_fingerprint = hashlib.sha256(Path(lot.csv).read_bytes()).hexdigest()
    return rows, source_fingerprint


def add_correction(rows):
    # Raw methods, train-selected methods and the 3+ endpoint are separate families.
    for endpoint in ("primary", "3plus"):
        for locked in (False, True):
            source = f"p_uniform_{endpoint}"
            family = {
                index: row[source] for index, row in enumerate(rows)
                if row[source] is not None
                and row["arm"] not in REFERENCES
                and (row["arm"] != row["method"]) == locked
            }
            for index, value in holm_adjusted(family).items():
                rows[index][f"p_holm_{endpoint}"] = value

    tail_family = {
        (index, endpoint): row[f"p_uniform_{endpoint}"]
        for index, row in enumerate(rows) for endpoint in ("5plus", "6")
        if row[f"p_uniform_{endpoint}"] is not None and row["arm"] not in REFERENCES
    }
    for (index, endpoint), value in holm_adjusted(tail_family).items():
        rows[index][f"p_holm_{endpoint}"] = value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--jobs", type=int, choices=range(1, 5), default=1)
    parser.add_argument("--cached-scores", type=Path, help="Use existing per-game strict-prefix score matrices")
    args = parser.parse_args()
    work = partial(evaluate, cached_scores=args.cached_scores)
    if args.jobs == 1:
        results = list(map(work, SETTINGS))
    else:
        with ProcessPoolExecutor(max_workers=args.jobs, mp_context=get_context("spawn")) as workers:
            results = list(workers.map(work, SETTINGS))
    rows = [row for grouped, _ in results for row in grouped]
    add_correction(rows)
    with args.output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    metadata = {
        "historical_models": list(ARCHIVED),
        "retired_models": list(RETIRED),
        "current_bank": list(CURRENT),
        "reference_pool_sizes": REFERENCE_POOLS,
        "archive_through": str(AS_OF),
        "data_sha256": dict(zip(SETTINGS, (h for _, h in results), strict=True)),
        "raw_tests_holm": sum(r["p_holm_primary"] is not None and r["arm"] == r["method"] for r in rows),
        "locked_tests_holm": sum(r["p_holm_primary"] is not None and r["arm"] != r["method"] for r in rows),
        "raw_3plus_tests_holm": sum(r["p_holm_3plus"] is not None and r["arm"] == r["method"] for r in rows),
        "locked_3plus_tests_holm": sum(r["p_holm_3plus"] is not None and r["arm"] != r["method"] for r in rows),
        "tail_tests_holm": sum(
            r[f"p_holm_{endpoint}"] is not None
            for r in rows for endpoint in ("5plus", "6")
        ),
    }
    args.output.with_suffix(".json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
