"""Replay the 20 November 2026 compact-catalog study on ten foreign main drums.

A date's top ten is computed from all strictly earlier draws. Of the eligible
dates after 250 prior draws, the first floor(70%) select the compact scorer
with the most 4+ events (alphabetical tie break). Its identity stays fixed for
the remaining dates. Same-day draws share a forecast and each counts as a target.
The registered history-seeded random and frequency scorers and the archived DMD
scorer are controls. The 4+, 5+ and 6 counts are pool intersections.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

if __name__ == "__main__":
    # Small DMD decompositions have a reproducible single-thread BLAS setup.
    for variable in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ[variable] = "1"

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
from scipy.stats import binom, binomtest

from loto_enterprise.benchmark.decision import expected_random_rate, holm_adjusted
from loto_enterprise.benchmark.methods import call_method
from loto_enterprise.core.draw_validation import valid_draw_matrix
from loto_enterprise.core.history import (
    chronological_history,
    history_dates,
    training_cutoffs,
)
from loto_enterprise.core.lotteries import require_lottery
from loto_enterprise.core.ranking import rank_by_score
from loto_enterprise.core.score_validation import has_usable_score_variance
from scripts.analysis.legacy_scoring.methods_wave2 import score_dmd_forecast

# Fix the last available date per archive so later appends preserve this replay.
ARCHIVE_END = {
    "de_lotto": "2026-11-18",
    "pl_lotto": "2026-11-19",
    "es_primitiva": "2026-11-19",
    "at_lotto": "2026-11-18",
    "be_lotto": "2026-11-18",
    "hu_hatos": "2026-11-19",
    "cz_sportka": "2026-11-18",
    "sk_loto": "2026-11-18",
    "bg_toto2": "2026-11-08",
    "eu_euromillions": "2026-11-17",
}
# In tie-breaking order. Later additions to the live catalog are outside this study.
COMPACT = (
    "compact_multiscale_shrink",
    "compact_renewal_hazard",
    "compact_transition_prior",
)
METHODS = (*COMPACT, "random", "frequency", "dmd_forecast")
THRESHOLDS = (4, 5, 6)
POOL_SIZE = 10
WARMUP = 250


def cumulative_counts(hits):
    return {str(t): int(np.count_nonzero(hits >= t)) for t in THRESHOLDS}


def evaluate_game(key):
    lottery = require_lottery(key)
    frame = chronological_history(pd.read_csv(ROOT / lottery.csv))
    dates = history_dates(frame)
    if dates.isna().any():
        raise ValueError(f"{key}: date missing in archive")
    frame = frame.loc[dates <= pd.Timestamp(ARCHIVE_END[key])].reset_index(drop=True)
    dates = history_dates(frame).to_numpy(dtype="datetime64[D]")
    if not len(dates) or str(dates[-1]) != ARCHIVE_END[key]:
        raise ValueError(f"{key}: the recorded final date is missing")

    cols = [f"n{i}" for i in range(1, lottery.draw_n + 1)]
    draws, valid = valid_draw_matrix(
        frame, cols, draw_n=lottery.draw_n, max_num=lottery.max_n
    )
    if not valid.all():
        raise ValueError(f"{key}: invalid main-drum numbers in archive")
    bounds = np.asarray(training_cutoffs(frame))
    origins = np.unique(bounds[bounds >= WARMUP])
    cut_day = len(origins) * 7 // 10
    if not 0 < cut_day < len(origins):
        raise ValueError(f"{key}: not enough eligible dates for the frozen split")

    hits = np.empty((len(draws) - origins[0], len(METHODS)), dtype=np.uint8)
    target_mask = np.zeros((len(draws), lottery.max_n + 1), dtype=np.uint8)
    np.put_along_axis(target_mask, draws, 1, axis=1)
    fallback_dates = dict.fromkeys(METHODS, 0)
    for start, stop in zip(origins, [*origins[1:], len(draws)], strict=True):
        past = draws[:start]
        scores_f, _ = call_method("frequency", past, lottery.max_n)
        for col, name in enumerate(METHODS):
            if name == "dmd_forecast":
                scores = score_dmd_forecast(past, lottery.max_n)
            elif name == "frequency":
                scores = scores_f
            else:
                scores, _ = call_method(
                    name, past, lottery.max_n, history_cutoffs=bounds[:start]
                )
            if not has_usable_score_variance(scores):
                fallback_dates[name] += 1
                scores = scores_f
            if set(scores) != set(range(1, lottery.max_n + 1)):
                raise ValueError(
                    f"{key}: {name} returned incomplete scores on {dates[start]}"
                )
            pool = rank_by_score(scores, POOL_SIZE)
            hits[start - origins[0] : stop - origins[0], col] = target_mask[
                start:stop, pool
            ].sum(axis=1)

    cut_draw = int(origins[cut_day] - origins[0])
    train, test = hits[:cut_draw], hits[cut_draw:]
    train_counts = (train[:, : len(COMPACT)] >= 4).sum(axis=0)
    selected = int(np.argmax(train_counts))
    rates = {
        str(t): expected_random_rate(lottery.max_n, lottery.draw_n, POOL_SIZE, t)
        for t in THRESHOLDS
    }
    reports = {}
    first_draw_of_day = origins[cut_day:] - origins[cut_day]
    for col, name in enumerate(METHODS):
        count = cumulative_counts(test[:, col])
        reports[name] = {
            "train_4plus": int(np.count_nonzero(train[:, col] >= 4)),
            "test": count,
            "first_draw_per_date_4plus": int(
                np.count_nonzero(test[first_draw_of_day, col] >= 4)
            ),
            "p_uniform_4plus": float(
                binomtest(
                    count["4"], len(test), rates["4"], alternative="greater"
                ).pvalue
            ),
        }

    report = {
        "geometry": lottery.geometry,
        "source": lottery.csv,
        "snapshot_csv_sha256": hashlib.sha256(
            frame.to_csv(index=False, lineterminator="\n").encode()
        ).hexdigest(),
        "archive_draws": len(draws),
        "archive_start": str(dates[0]),
        "train_start": str(dates[origins[0]]),
        "train_end": str(dates[origins[cut_day] - 1]),
        "train_dates": cut_day,
        "train_draws": len(train),
        "test_start": str(dates[origins[cut_day]]),
        "test_end": str(dates[-1]),
        "test_dates": len(origins) - cut_day,
        "test_draws": len(test),
        "fair_rates": rates,
        "frozen_method": COMPACT[selected],
        "fallback_dates": {
            name: count for name, count in fallback_dates.items() if count
        },
        "methods": reports,
    }
    return report, np.column_stack((test, test[:, selected]))


def evaluate():
    games = {}
    holdouts = []
    fair_mass = np.array([1.0])
    expected = dict.fromkeys(map(str, THRESHOLDS), 0.0)
    frozen_p = {}
    method_game_p = {}
    for key in ARCHIVE_END:
        game, hits = evaluate_game(key)
        games[key] = game
        holdouts.append(hits)
        count = game["test_draws"]
        rates = game["fair_rates"]
        # The null assumes independent fair draws. Its 4+ probability differs
        # between geometries, so convolve the ten game-specific binomials.
        fair_mass = np.convolve(
            fair_mass, binom.pmf(np.arange(count + 1), count, rates["4"])
        )
        for t, rate in rates.items():
            expected[t] += count * rate
        frozen_p[key] = game["methods"][game["frozen_method"]]["p_uniform_4plus"]
        for name in COMPACT:
            method_game_p[(key, name)] = game["methods"][name]["p_uniform_4plus"]
    for (key, name), p in holm_adjusted(method_game_p).items():
        games[key]["methods"][name]["p_holm_all_method_games"] = p
    for key, p in holm_adjusted(frozen_p).items():
        games[key]["p_holm_frozen_across_games"] = p

    methods = {}
    joined = np.concatenate(holdouts)
    for name, hits in zip((*METHODS, "frozen"), joined.T, strict=True):
        counts = cumulative_counts(hits)
        methods[name] = {
            "test": counts,
            "p_uniform_4plus": float(fair_mass[counts["4"] :].sum()),
        }
    for name, p in holm_adjusted(
        {name: methods[name]["p_uniform_4plus"] for name in COMPACT}
    ).items():
        methods[name]["p_holm_across_compact_methods"] = p
    return {
        "pool_size": POOL_SIZE,
        "warmup_draws": WARMUP,
        "selection": "Most 4+ on first floor(70%) of eligible dates in each game; ties by method name.",
        "controls": "Registered history-seeded random, recency-weighted frequency, archived dmd_forecast.",
        "scope": "Main-drum top ten by raw score; EuroMillions stars and other bonus balls excluded.",
        "games": games,
        "aggregate": {
            "train_draws": sum(game["train_draws"] for game in games.values()),
            "test_draws": len(joined),
            "test_game_dates": sum(game["test_dates"] for game in games.values()),
            "expected_test": expected,
            "methods": methods,
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).with_suffix(".json"),
        help="write JSON here (default: beside this script)",
    )
    args = parser.parse_args()
    args.output.write_text(
        json.dumps(evaluate(), indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
