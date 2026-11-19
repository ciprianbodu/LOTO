"""Exploratory secondary replay of the live LR selector on previously studied holdouts.

The pooled uniform tails convolve the three binomial distributions with the
distinct exact ticket probabilities of the official schemes. This assumes
independent uniform draws within and across games. These unadjusted p-values
describe the retrospective experiment. They do not establish predictive superiority.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from itertools import pairwise
from pathlib import Path
from time import perf_counter

import numpy as np
import pandas as pd
from scipy.stats import binom

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from covering.lr_schemes import scheme_odds, scheme_tickets
from covering.lr_selection import lr_scheme_signature, resolve_lr_scheme
from loto_enterprise.benchmark.methods import call_method
from loto_enterprise.core.draw_validation import valid_draw_matrix
from loto_enterprise.core.dynamic_winner import (
    CANDIDATES,
    DYNAMIC_VERSION,
    choose_dynamic_winner,
    dynamic_forecasts,
)
from loto_enterprise.core.history import (
    chronological_history,
    history_dates,
    training_cutoffs,
)
from loto_enterprise.core.pool_selection import select_pool_from_scores
from loto_enterprise.core.ranking import rank_by_score
from scripts.analysis.lr_pool_study_2026_11_04 import load_source

HERE = Path(__file__).resolve().parent
ARCHIVE = HERE / "tail_1000_results.json"
SETTINGS = {"max_run": 2, "penalty_draws": 0}
REFERENCES = ("frequency", "random")
AS_OF = "2026-11-15"
# Geometries, dates and control ticket counts fixed in the 1,000-recipe study.
GAMES = (
    ("loto_6_49", "6/49", 49, 6, 6, 9, 48, "2019-11-07", 718, (2, 0, 0)),
    ("loto_5_40", "5/40", 40, 6, 5, 8, 16, "2022-03-27", 465, (5, 0)),
    ("joker", "joker", 45, 5, 5, 10, 24, "2021-04-04", 592, (3, 0)),
)


def as_counts(hits, thresholds):
    return {str(t): int(np.count_nonzero(hits >= t)) for t in thresholds}


def joker_matches(dates, bounds, targets):
    frame = chronological_history(pd.read_csv(ROOT / "_ISTORIC" / "joker.csv")).iloc[
        : len(dates)
    ]
    if not np.array_equal(history_dates(frame), dates) or not np.array_equal(
        training_cutoffs(frame), bounds
    ):
        raise ValueError("Datele urnei Joker diferă de cele ale numerelor principale")
    bonus, valid = valid_draw_matrix(frame, ["joker"], draw_n=1, max_num=20)
    if not valid.all():
        raise ValueError("Număr Joker invalid")
    picks = {}
    for origin in np.unique(bounds[targets]):
        scores, _ = call_method(
            "frequency", bonus[:origin], 20, history_cutoffs=bounds[:origin]
        )
        picks[origin] = rank_by_score(scores, 1)[0]
    return np.array([picks[bounds[i]] == bonus[i, 0] for i in targets])


def evaluate(game, original):
    (
        key,
        label,
        universe,
        draw_n,
        pick,
        size,
        code,
        first_day,
        n_expected,
        known_random,
    ) = game
    matrix, dates, bounds, digest = load_source(key, universe, draw_n)
    end = int(np.searchsorted(dates, np.datetime64(AS_OF), side="right"))
    matrix = np.ascontiguousarray(matrix[:end], dtype=np.int64)
    dates, bounds = dates[:end], bounds[:end]
    targets = np.flatnonzero(dates >= np.datetime64(first_day))
    scheme = resolve_lr_scheme(label, code, size)
    signature = lr_scheme_signature(label, code, size)
    if len(targets) != n_expected or not len(targets):
        raise ValueError(f"Perioadă istorică incompletă: {key}")
    origins = sorted(set(map(int, bounds[targets])))
    split = {
        "first_date": str(dates[targets[0]].astype("datetime64[D]")),
        "last_date": str(dates[targets[-1]].astype("datetime64[D]")),
        "draws": len(targets),
        "days": len(origins),
    }
    if split["first_date"] != first_day or split["last_date"] != AS_OF:
        raise ValueError(
            f"Arhiva sau perioada de test diferă de studiul inițial: {key}"
        )
    if original and (
        original["lr_signature"] != signature
        or any(original["holdout"][k] != v for k, v in split.items())
    ):
        raise ValueError(f"Rezultate istorice incompatibile pentru {key}")
    next_origin = len(matrix)
    started = perf_counter()
    predictions = dynamic_forecasts(
        matrix, bounds, scheme, [*origins, next_origin], **SETTINGS
    )
    batch_seconds = perf_counter() - started

    by_strategy = {strategy: {} for strategy in ("dynamic", *REFERENCES)}
    methods, reasons = [], []
    for origin in origins:
        dynamic_scores, audit = predictions[origin]
        methods.append(audit["method"])
        reasons.append(audit["reason"])
        scores = {"dynamic": dynamic_scores}
        for ref in REFERENCES:
            scores[ref], _ = call_method(
                ref, matrix[:origin], universe, history_cutoffs=bounds[:origin]
            )
        for strategy, row in scores.items():
            pool = select_pool_from_scores(
                row, size, set(), max_num=universe, max_consecutive_run=2
            )
            tickets = scheme_tickets(scheme, pool)
            by_strategy[strategy][origin] = (
                set(pool),
                [set(ticket) for ticket in tickets],
            )

    bonus = joker_matches(dates, bounds, targets) if key == "joker" else None
    outcomes = {}
    ticket_events = {}
    for strategy, predictions_by_day in by_strategy.items():
        pool_hits, ticket_hits = [], []
        for i in targets:
            actual = set(matrix[i])
            pool, tickets = predictions_by_day[bounds[i]]
            pool_hits.append(len(pool & actual))
            ticket_hits.append(max(len(ticket & actual) for ticket in tickets))
        outcomes[strategy] = {
            "pool": as_counts(np.asarray(pool_hits), range(4, draw_n + 1)),
            "ticket": as_counts(np.asarray(ticket_hits), range(4, pick + 1)),
        }
        ticket_events[strategy] = np.asarray(ticket_hits)
        if bonus is not None:
            outcomes[strategy]["ticket_with_joker"] = as_counts(
                np.asarray(ticket_hits)[bonus], range(4, pick + 1)
            )
    if tuple(outcomes["random"]["ticket"].values()) != known_random:
        raise AssertionError(f"{key}: random differs from the frozen ticket counts")
    if original:
        for ref in REFERENCES:
            if original["holdout"]["strategies"][ref] != outcomes[ref]:
                raise AssertionError(
                    f"{key}: {ref} differs from the frozen holdout counts"
                )
    paired = {}
    for t in range(4, pick + 1):
        dynamic = ticket_events["dynamic"] >= t
        random = ticket_events["random"] >= t
        paired[str(t)] = {
            "dynamic_only": int(sum(dynamic & ~random)),
            "random_only": int(sum(random & ~dynamic)),
            "both": int(sum(dynamic & random)),
            "neither": int(sum(~dynamic & ~random)),
        }

    # The batch call above sees the full archive. An independent call physically
    # truncates it, exercising the entry point used by live production.
    checked = []
    for origin in (origins[0], next_origin):
        t0 = perf_counter()
        standalone = choose_dynamic_winner(
            matrix[:origin], bounds[:origin], scheme, **SETTINGS
        )
        if standalone != predictions[origin]:
            raise AssertionError(f"{key}: batch/live mismatch at origin {origin}")
        checked.append(
            {
                "prefix_rows": origin,
                "method": standalone[1]["method"],
                "seconds": round(perf_counter() - t0, 3),
            }
        )
    return {
        **split,
        "sha256_source_csv_at_replay": digest,
        "lr_signature": signature,
        "lr_code": code,
        "pool_size": size,
        "tickets_per_draw": scheme.size,
        "outcomes_by_draw": outcomes,
        "paired_ticket_vs_random": paired,
        "uniform_ticket_probabilities": {
            str(t): scheme_odds(scheme)[t] for t in range(4, pick + 1)
        },
        "method_changes_between_days": sum(a != b for a, b in pairwise(methods)),
        "distinct_effective_methods": len(set(methods)),
        "method_days": dict(Counter(methods)),
        "reason_days": dict(Counter(reasons)),
        "fallback_days": sum(reason != "winner" for reason in reasons),
        "fallback_draws": sum(
            predictions[bounds[i]][1]["reason"] != "winner" for i in targets
        ),
        "next_forecast_after_archive": predictions[next_origin][1],
        "live_parity_checks": checked,
        "random_tickets_match_frozen_counts": True,
        "optional_archive_references_checked": list(REFERENCES) if original else [],
        "batch_seconds_including_next_forecast": round(batch_seconds, 3),
        "validation_seconds_including_parity": round(perf_counter() - started, 3),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, default=HERE / "dynamic_lr_validation_2026_11_19.json"
    )
    args = parser.parse_args()
    frozen = json.loads(ARCHIVE.read_bytes())["games"] if ARCHIVE.is_file() else {}
    output = {
        "analysis": "Analiză secundară exploratorie pe perioadele finale folosite în studiul anterior.",
        "selector_version": DYNAMIC_VERSION,
        "competing_methods": len(CANDIDATES),
        "joker_bonus_comparator": "registered frequency for all three strategies",
        "settings": SETTINGS,
        "games": {},
    }
    for game in GAMES:
        result = evaluate(game, frozen.get(game[0]))
        output["games"][game[0]] = result
        print(game[0], result["outcomes_by_draw"], result["reason_days"], flush=True)
    pooled = {}
    for t in ("4", "5"):
        distribution = np.array([1.0])
        expected = 0.0
        observed = 0
        paired = Counter()
        for result in output["games"].values():
            n, p = result["draws"], result["uniform_ticket_probabilities"][t]
            distribution = np.convolve(distribution, binom.pmf(np.arange(n + 1), n, p))
            expected += n * p
            observed += result["outcomes_by_draw"]["dynamic"]["ticket"][t]
            paired.update(result["paired_ticket_vs_random"][t])
        pooled[t] = {
            "paired_vs_registered_random": dict(paired),
            "dynamic_observed": observed,
            "uniform_expected": expected,
            "uniform_upper_tail": float(np.clip(distribution[observed:].sum(), 0, 1))
            if observed
            else 1.0,
        }
    output["all_games_ticket_endpoints"] = pooled
    args.out.write_text(
        json.dumps(output, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
