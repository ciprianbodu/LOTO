"""Reproduce the Romanian compact-catalog experiment through 2026-11-19.

Run «python -m scripts.analysis.compact_ro_2026_11_20 --help» from the repository.
By default the archived fixed methods are the previously measured training winner
and retrospective leader for each setting. --rescan-legacy derives both from
the 56 original nonrandom methods. Both modes replay every reported forecast.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import sys
from concurrent.futures import ProcessPoolExecutor
from functools import partial
from itertools import chain
from pathlib import Path
from tempfile import NamedTemporaryFile

import numpy as np
import scipy
from scipy.stats import binomtest

from covering.lr_selection import resolve_lr_scheme
from loto_enterprise.benchmark import methods
from loto_enterprise.benchmark.decision import expected_random_rate, holm_adjusted
from loto_enterprise.core.dynamic_pools import ranked_pools
from loto_enterprise.core.dynamic_winner import _forecast_origins
from loto_enterprise.core.lotteries import lottery_by_bench_key
from loto_enterprise.core.pool_selection import select_pool_from_scores
from loto_enterprise.core.score_validation import has_usable_score_variance
from scripts.analysis import legacy_methods_2026_11_20 as legacy
from scripts.analysis.nested_wfv_2026_11_18 import read_draws
from scripts.analysis.validate_dynamic_lr_2026_11_19 import (
    _archived_four_plus_winner,
    archived_forecasts,
)

HERE = Path(__file__).resolve().parent
AS_OF = np.datetime64("2026-11-19")
# Declaration order determined ties in the original selector and its control.
STUDY_METHODS = (
    "compact_multiscale_shrink",
    "compact_transition_prior",
    "compact_renewal_hazard",
)
CURRENT = ("frequency", *STUDY_METHODS, "random")
# K, official LR code, previous training winner, previous holdout leader.
# The last entry is descriptive. It cannot participate in the prospective rule.
SETTINGS = {
    "loto_6_49": (
        (9, 48, "vlmm_self_k3", "logit_pooled_feats"),
        (16, 59, "runs_persistence", "dmd_forecast"),
    ),
    "loto_5_40": (
        (8, 16, "pair_lift_last", "pair_lift_last"),
        (16, 37, "drift_linear", "dmd_forecast"),
        (19, 48, "cusum_burst", "neighbor_adjacent"),
    ),
    "joker_urna1": (
        (10, 24, "frequency", "rwr_last_draw"),
        (15, 12, "ssa_forecast", "bayes_dirichlet_forget"),
    ),
    "joker_urna2": (
        (1, None, "runs_persistence", "ridge_pooled_feats"),
        (5, None, "season_period2", "knn_draw_similarity"),
    ),
}


def archive(key):
    second = key == "joker_urna2"
    lot = lottery_by_bench_key(key)
    matrix, dates, bounds, _, universe, _ = read_draws(lot, second)
    end = int(np.searchsorted(dates, AS_OF, side="right"))
    if end == 0 or dates[end - 1] != AS_OF:
        raise ValueError(f"Missing last study date in {key}")
    matrix = np.ascontiguousarray(matrix[:end], dtype=np.int64)
    dates, bounds = dates[:end], bounds[:end]
    days = np.unique(bounds[bounds >= 250])
    return lot, matrix, dates, bounds, days, universe


def cache_name(directory, key, names, matrix, bounds):
    digest = hashlib.sha256()
    digest.update(repr((key, names, sys.version, np.__version__, scipy.__version__)).encode())
    digest.update(matrix.tobytes())
    digest.update(bounds.tobytes())
    source = HERE.parents[1] / "loto_enterprise"
    files = [Path(__file__), HERE / "legacy_methods_2026_11_20.py",
             *sorted((HERE / "legacy_scoring").glob("*.py")),
             *sorted((source / "benchmark").glob("methods*.py")),
             *(source / "core" / f for f in ("dynamic_pools.py", "ranking.py", "pool_selection.py", "score_validation.py"))]
    for path in files:
        digest.update(path.read_bytes())
    return directory / f"{key}-{digest.hexdigest()}.npz"


def static_outcomes(key, names, matrix, bounds, days, universe, directory):
    path = cache_name(directory, key, names, matrix, bounds) if directory else None
    if path and path.exists():
        with np.load(path, allow_pickle=False) as cache:
            return {int(k): cache[k] for k in cache.files}

    scores = np.empty((len(days), len(names), universe), dtype=float)
    reported = set()
    for day, origin in enumerate(days):
        for j, name in enumerate(names):
            caller = methods.call_method if name in CURRENT else legacy.call_method
            try:
                values, _ = caller(name, matrix[:origin], universe, history_cutoffs=bounds[:origin])
                row = np.array([values[ball] for ball in range(1, universe + 1)])
                if (len(values) != universe or not np.isfinite(row).all() or (row < 0).any()
                        or (name != "frequency" and not has_usable_score_variance(values))):
                    raise ValueError("Invalid score distribution")
                scores[day, j] = row
            except Exception as exc:
                if name in ("frequency", "random"):
                    raise
                if name not in reported:
                    print(f"{key}: {name} uses frequency at prefix {origin} ({exc})", file=sys.stderr)
                    reported.add(name)
                scores[day, j] = scores[day, 0]  # The first column always holds frequency.

    target = matrix[days[0]:]
    row_day = np.searchsorted(days, bounds[days[0]:])
    actual = np.zeros((len(target), universe + 1), dtype=bool)
    actual[np.arange(len(target))[:, None], target] = True
    hits = {}
    for k, _, _, _ in SETTINGS[key]:
        pools = ranked_pools(
            scores.reshape(-1, universe), universe=universe, pool_size=k,
            max_run=0 if key == "joker_urna2" else 2,
        ).reshape(len(days), len(names), k)
        hits[k] = actual[np.arange(len(target))[:, None, None], pools[row_day]].sum(axis=2, dtype=np.uint8)
    if path:
        directory.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=directory, suffix=".npz", delete=False) as handle:
            np.savez_compressed(handle, **{str(k): h for k, h in hits.items()})
        Path(handle.name).replace(path)
    return hits


def fixed_winner(candidates, names, counts):
    return min(candidates, key=lambda n: (-int(counts[names.index(n)]), n != "frequency", n))


def archived_v4_forecasts(matrix, bounds, scheme, origins, **settings):
    """Replay the four selectable methods in the original 2026-11-20 study."""
    candidates = CURRENT[:-1]
    return _forecast_origins(
        matrix, bounds, scheme, origins,
        candidates=candidates,
        select_winner=_archived_four_plus_winner,
        selection_metadata={
            "version": "dynamic-lr-v4-compact-pool-4plus",
            "hit_scope": "pool",
            "pool_hit_target": 4,
            "pool_size": scheme.n,
            "candidate_methods": list(candidates),
            "control_method": "random",
        },
        **settings,
    )


def dynamic_hits(matrix, bounds, days, scheme, *, archived):
    settings = {"max_run": 2, "penalty_draws": 0}
    if archived:
        forecasts = archived_forecasts(matrix, bounds, scheme, days, version="dynamic-lr-v3-pool-4plus", settings=settings)
    else:
        forecasts = archived_v4_forecasts(matrix, bounds, scheme, days, **settings)
    outcomes = []
    for start, end in zip(days, np.r_[days[1:], len(matrix)], strict=True):
        scores, detail = forecasts[start]
        if detail["history_rows"] != start:
            raise ValueError("Forecast origin differs from its history length")
        selected = select_pool_from_scores(scores, scheme.n, set(), max_num=len(scores), max_consecutive_run=2)
        outcomes.extend(np.isin(matrix[start:end], selected).sum(axis=1))
    return np.asarray(outcomes)


def paired(sample, reference):
    wins = int((sample & ~reference).sum())
    losses = int((reference & ~sample).sum())
    p = binomtest(wins, wins + losses, .5, alternative="greater").pvalue if wins + losses else 1.
    return wins, losses, float(p)


def evaluate(key, *, rescan, cache_dir):
    lot, matrix, dates, bounds, days, universe = archive(key)
    second = key == "joker_urna2"
    threshold = 1 if second else 4
    split = len(days) * 7 // 10
    cut = int(days[split] - days[0])
    old_candidates = sorted(set(legacy.METHODS) - {"random"})
    old_names = old_candidates if rescan else sorted({n for _, _, train, test in SETTINGS[key] for n in (train, test)})
    names = tuple(dict.fromkeys((*CURRENT, *old_names)))
    hits = static_outcomes(key, names, matrix, bounds, days, universe, cache_dir)
    draw_n = matrix.shape[1]
    rows = []
    for k, code, locked, previous_leader in SETTINGS[key]:
        h = hits[k]
        train = (h[:cut] >= threshold).sum(axis=0)
        test = (h[cut:] >= threshold).sum(axis=0)
        old_winner = fixed_winner(old_candidates, names, train) if rescan else locked
        old_leader = fixed_winner(old_candidates, names, test) if rescan else previous_leader
        chosen = fixed_winner(CURRENT[:-1], names, train)
        new_leader = fixed_winner(STUDY_METHODS, names, test)
        trials = {name: name for name in CURRENT}
        trials.update(static_new=chosen, only_new=fixed_winner(STUDY_METHODS, names, train),
                      static_old=old_winner, retrospective_new=new_leader, retrospective_old=old_leader)
        series = {arm: h[cut:, names.index(method)] for arm, method in trials.items()}
        if code is not None:
            scheme = resolve_lr_scheme(lot.geometry, code, k)
            for arm in ("dynamic_v3", "dynamic_v4"):
                series[arm] = dynamic_hits(matrix, bounds, days[split:], scheme, archived=arm == "dynamic_v3")
        random = series["random"] >= threshold
        rate = expected_random_rate(universe, draw_n, k, threshold)
        for arm, values in series.items():
            method = trials.get(arm, "")
            success = values >= threshold
            total = int(success.sum())
            w, l, prob = paired(success, random)
            row = dict(game=key, k=k, lr_code=code, threshold=threshold, eligible_start=dates[days[0]],
                       selection_end=dates[days[split]-1], holdout_start=dates[days[split]], holdout_end=dates[-1],
                       train_draws=cut, test_draws=len(success), expected_uniform=rate * len(success),
                       arm=arm, method=method, train=int(train[names.index(method)]) if method else None, test=total,
                       test5plus=int((values >= 5).sum()) if draw_n >= 5 else None,
                       test6=int((values == 6).sum()) if draw_n == 6 else None,
                       exact_binomial_p=float(binomtest(total, len(success), rate, alternative="greater").pvalue),
                       paired_wins=w, paired_losses=l, paired_greater_p=prob, uniform_p_holm=None,
                       paired_p_holm=None, paired_old_p=None, paired_old_p_holm=None)
            comparator = {"static_new": "static_old", "dynamic_v4": "dynamic_v3"}.get(arm)
            if comparator:
                row["paired_old_p"] = paired(success, series[comparator] >= threshold)[2]
            rows.append(row)
    return rows


def adjust(rows, source, dest):
    corrected = holm_adjusted({i: row[source] for i, row in enumerate(rows)})
    for i, row in enumerate(rows):
        row[dest] = corrected[i]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=HERE / "compact_ro_2026_11_20.csv")
    parser.add_argument("--cache-dir", type=Path, help="Optional cache for fixed scorer outcomes")
    parser.add_argument("--jobs", type=int, choices=range(1, 5), default=1)
    parser.add_argument("--rescan-legacy", action="store_true", help="Rerun all 56 original selectable methods")
    args = parser.parse_args()
    run = partial(evaluate, rescan=args.rescan_legacy, cache_dir=args.cache_dir)
    if args.jobs == 1:
        rows = list(chain.from_iterable(map(run, SETTINGS)))
    else:
        with ProcessPoolExecutor(max_workers=args.jobs) as workers:
            rows = list(chain.from_iterable(workers.map(run, SETTINGS)))
    for group in (STUDY_METHODS, {"static_new"}, {"dynamic_v4"}):
        relevant = [r for r in rows if r["arm"] in group]
        adjust(relevant, "exact_binomial_p", "uniform_p_holm")
        adjust(relevant, "paired_greater_p", "paired_p_holm")
        if relevant[0]["paired_old_p"] is not None:
            adjust(relevant, "paired_old_p", "paired_old_p_holm")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(f"{len(rows)} rows written to {args.output}")


if __name__ == "__main__":
    main()
