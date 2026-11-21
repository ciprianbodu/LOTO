"""Causal comparison of scorer training lengths on every registered game.

Run from the repository root with python -m scripts.analysis.history_fit_2026_11_18
--output scratch/history_fit_2026_11_18. All predictions for a date use earlier
draw dates only. This retrospective archive has been examined in past studies.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from covering.lr_schemes import scheme_odds
from covering.lr_selection import resolve_lr_scheme
from scripts.analysis.legacy_methods_2026_11_20 import call_method
from loto_enterprise.core.draw_validation import valid_draw_matrix
from loto_enterprise.core.history import chronological_history, history_dates, training_cutoffs
from loto_enterprise.core.lotteries import GAMES
from loto_enterprise.core.pool_selection import select_pool_from_scores
from scripts.analysis.hit_evidence_2026_11_04 import holm, hypergeom_tail
from scripts.analysis.lr_pool_study_2026_11_04 import exact_lookup, safe_scores


ROOT = Path(__file__).resolve().parents[2]
FRACTIONS = (100, 70, 30, 10)
# These are specific Romanian settings from the earlier archived audit.
ROMANIAN = {
    "6/49": (9, 48, "naive_bayes_last"),
    "joker": (10, 24, "runs_persistence"),
    "5/40": (8, 16, "croston_interval"),
}
PROTOCOL = {
    "fit_fractions_percent": FRACTIONS,
    "unit": "Most recent ceil(pct * count / 100) distinct preceding draw dates, including all rows on each date.",
    "warmup_dates": 250,
    "calibration_share_percent": 70,
    "test": "Remaining last 30% of eligible dates; identical targets for all lengths. Earlier archive reuse makes this exploratory.",
    "selection": "Calibration maximum event count, then count at secondary threshold, then sum of primary-surface hits, then longer training. Frozen for test.",
    "primary": "Best official LR ticket >=5 in Romania, main pool K10 >=5 abroad, top-1 Joker second urn.",
    "secondary": "Best ticket/pool >=4 for main urns. Top-1 remains the only threshold for the second urn.",
    "scorers": "Registry frequency (recency weighted) for every urn; sensitivity for the previously selected Romanian scorers.",
    "romanian_schemes_and_sensitivity_scorers": ROMANIAN,
    "joker_second_sensitivity_scorer": "ridge_pooled_feats",
    "consecutive_limit": 2,
    "inference": "Paired success differences vs full history, conditional null E[short success - full success | past] <= 0. Fixed sign-bet mixture of 0.10,0.25,0.50,0.75 with 1/e final p-value. Holm across all shorter and calibrated rules/scorers/urns. Uniform draws imply the paired null at fixed geometry. Ties leave all bets unchanged.",
}
BETS = (0.1, 0.25, 0.5, 0.75)


def fit_slice(starts, day, pct):
    size = (day * pct + 99) // 100
    return slice(int(starts[day - size]), int(starts[day]))


def choose_window(hits, primary, secondary):
    # With single-pick there is only one target. Remaining ties prefer more data.
    return max(range(len(FRACTIONS)), key=lambda idx: (
        int(np.count_nonzero(hits[:, idx] >= primary)),
        int(np.count_nonzero(hits[:, idx] >= secondary)),
        int(hits[:, idx].sum()),
        FRACTIONS[idx],
    ))


def paired(short, full, threshold):
    a, b = short >= threshold, full >= threshold
    wins = int(np.count_nonzero(a & ~b))
    losses = int(np.count_nonzero(b & ~a))
    logs = np.array([wins * math.log1p(x) + losses * math.log1p(-x) for x in BETS])
    log_e = float(np.logaddexp.reduce(logs) - math.log(len(BETS)))
    return {"extra": wins, "lost": losses, "log_e": log_e, "p": math.exp(-max(0., log_e))}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_urn(game, second):
    path = ROOT / game.csv
    frame = chronological_history(pd.read_csv(path))
    dates = history_dates(frame).to_numpy()
    cutoffs = np.asarray(training_cutoffs(frame))
    starts = np.flatnonzero(cutoffs == np.arange(len(frame)))
    ends = np.r_[starts[1:], len(frame)]
    scheme = None
    methods = ["frequency"]
    lookup = None
    if second:
        universe, draw_n, k = game.geo.second.max_n, game.geo.second.draw_n, 1
        columns = list(game.geo.second.columns)
        methods.append(PROTOCOL["joker_second_sensitivity_scorer"])
    else:
        universe, draw_n, k = game.max_n, game.draw_n, 10
        columns = [f"n{i}" for i in range(1, draw_n + 1)]
        if game.is_romanian:
            k, code, scorer = ROMANIAN[game.game_id]
            methods.append(scorer)
            scheme = resolve_lr_scheme(game.geometry, code, k)
            lookup = exact_lookup(k, scheme.variants)

    matrix, valid = valid_draw_matrix(frame, columns, draw_n=draw_n, max_num=universe)
    if not np.all(valid):
        raise ValueError(f"Invalid draws in {path}")
    primary, secondary = (1, 1) if second else (5, 4)
    warmup = PROTOCOL["warmup_dates"]
    split_day = warmup + (len(starts) - warmup) * PROTOCOL["calibration_share_percent"] // 100
    first, split = int(starts[warmup]), int(starts[split_day])
    size = (len(frame) - first, len(FRACTIONS), len(methods))
    pool_hits = np.zeros(size, dtype=np.int8)
    ticket_hits = np.zeros_like(pool_hits) if scheme is not None else None
    fallback = np.zeros((len(FRACTIONS), len(methods)), dtype=np.int32)

    for day in range(warmup, len(starts)):
        start, end = int(starts[day]), int(ends[day])
        actuals = [set(map(int, row)) for row in matrix[start:end]]
        for fi, pct in enumerate(FRACTIONS):
            scope = fit_slice(starts, day, pct)
            past = matrix[scope]
            boundaries = cutoffs[scope] - scope.start
            freq = call_method("frequency", past, universe)[0]
            for mi, method in enumerate(methods):
                if mi == 0:
                    scores = freq
                else:
                    scores, reason, _ = safe_scores(method, past, universe, boundaries, freq)
                    fallback[fi, mi] += int(reason is not None)
                pool = select_pool_from_scores(scores, k, set(), max_num=universe,
                                               max_consecutive_run=PROTOCOL["consecutive_limit"])
                if len(pool) != k:
                    raise ValueError(f"Invalid pool size for {method} and {game.game_id}")
                for ri, actual in enumerate(actuals, start=start-first):
                    mask = sum(1 << pos for pos, n in enumerate(pool) if n in actual)
                    pool_hits[ri, fi, mi] = mask.bit_count()
                    if lookup is not None:
                        ticket_hits[ri, fi, mi] = lookup[mask]

    hits = ticket_hits if scheme is not None else pool_hits
    key = game.bench_key_urna2 if second else game.bench_key
    output = {
        "urn": key, "geometry": [universe, draw_n, k], "source": game.csv,
        "history_sha256": digest(path), "scheme": scheme.code if scheme else None,
        "ticket_count": scheme.size if scheme else None,
        "uniform_primary_probability": scheme_odds(scheme)[primary] if scheme else hypergeom_tail(universe, draw_n, k, primary),
        "total_rows": len(frame), "total_dates": len(starts), "calibration_rows": split-first,
        "test_rows": len(frame)-split, "calibration_first": str(dates[first])[:10],
        "test_first": str(dates[split])[:10], "last": str(dates[-1])[:10], "methods": {},
    }
    for mi, method in enumerate(methods):
        cal = hits[:split-first, :, mi]
        test = hits[split-first:, :, mi]
        test_pool = pool_hits[split-first:, :, mi]
        selected = choose_window(cal, primary, secondary)
        comparisons = {str(p): paired(test[:, i], test[:, 0], primary)
                       for i, p in enumerate(FRACTIONS) if p != 100}
        comparisons["calibrated"] = paired(test[:, selected], test[:, 0], primary)
        by_fraction = {}
        for i, pct in enumerate(FRACTIONS):
            by_fraction[str(pct)] = {
                "test_primary": int(np.count_nonzero(test[:, i] >= primary)),
                "test_secondary": int(np.count_nonzero(test[:, i] >= secondary)),
                "test_pool_primary": int(np.count_nonzero(test_pool[:, i] >= primary)),
                "calibration_primary": int(np.count_nonzero(cal[:, i] >= primary)),
                "calibration_secondary": int(np.count_nonzero(cal[:, i] >= secondary)),
                "test_mean": float(np.mean(test[:, i])),
                "fallback_dates": int(fallback[i, mi]),
            }
        output["methods"][method] = {"fractions": by_fraction, "selected_percent": FRACTIONS[selected],
                                      "comparisons": comparisons}
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    protocol = {"protocol": PROTOCOL, "script_sha256": digest(Path(__file__)),
                "history_sha256": {g.csv: digest(ROOT / g.csv) for g in GAMES}}
    (args.output / "protocol.json").write_text(json.dumps(protocol, indent=2) + "\n", encoding="utf-8")
    outputs = []
    for game in GAMES:
        for second in ((False, True) if game.bench_key_urna2 else (False,)):
            result = run_urn(game, second)
            result_path = args.output / (result["urn"] + ".json")
            result_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            print(result["urn"], result["test_rows"], flush=True)
            outputs.append(result)
    tests = [comparison for result in outputs for data in result["methods"].values()
             for comparison in data["comparisons"].values()]
    for comparison, adjusted in zip(tests, holm([t["p"] for t in tests]), strict=True):
        comparison["holm"] = adjusted
    report = {**protocol, "results": outputs, "tests": len(tests)}
    (args.output / "results.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
