"""Retrospective method diagnostics on disjoint windows; no production writes.

These histories were available during method development: this is NOT an
untouched holdout. Rates measure pool hits, not ticket prizes or profit.
5/40 uses the first five drawn numbers, consistently with the scoring bench.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
for _name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[_name] = "1"

import numpy as np
from scipy.stats import binomtest

from loto_enterprise.benchmark.decision import (
    EXCLUDED_FROM_PRODUCTION,
    expected_random_rate,
)
from loto_enterprise.benchmark.methods import METHODS
from loto_enterprise.benchmark.runner import discover_games, load_draws
from loto_enterprise.core.ranking import rank_by_score
from loto_enterprise.core.score_validation import (
    has_usable_score_variance,
)

PROTECTED = [
    "best_methods.json",
    "bench_results/folds.csv",
    "bench_results/report.json",
    "pool_history.json",
    ".ui_state.json",
]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def holm_adjust(pvalues):
    """Holm family-wise correction, valid even for dependent comparisons."""
    order = sorted(range(len(pvalues)), key=pvalues.__getitem__)
    result = [1.0] * len(pvalues)
    previous = 0.0
    for rank, index in enumerate(order):
        previous = max(previous, min(1.0, pvalues[index] * (len(order) - rank)))
        result[index] = previous
    return result


def audit_game(game, window_size, windows):
    history_before = digest(Path(game.csv_path))
    draws = load_draws(game)
    count = window_size * windows
    start = len(draws) - count
    if start < 50:
        raise ValueError(f"Insufficient training history for {game.key}")
    pools, targets = (
        ((1,), (1,)) if game.is_single_pick else ((6, 8, 10, 12, 16), (3, 4))
    )
    rows, failures, timing = [], [], []
    expected_keys = set(range(1, game.max_num + 1))
    draws.setflags(write=False)
    for name, (scorer, *_meta) in sorted(METHODS.items()):
        started = time.perf_counter()
        hits = {k: [[] for _ in range(windows)] for k in pools}
        ties = {k: [0] * windows for k in pools}
        flat = [0] * windows
        for offset, target_index in enumerate(range(start, len(draws))):
            window = offset // window_size
            cutoff = game.history_cutoffs[target_index]
            assert 0 <= cutoff <= target_index
            try:
                scores = scorer(draws[:cutoff], game.max_num)
                values = np.asarray(list(scores.values()), dtype=float)
                if (
                    set(scores) != expected_keys
                    or not np.isfinite(values).all()
                    or ((values < 0) | (values > 1)).any()
                ):
                    raise ValueError("Incomplete/nonfinite/unnormalized scores")
                if not has_usable_score_variance(scores):
                    flat[window] += 1
                    continue
                ranked = rank_by_score(scores, game.max_num)
                actual = set(map(int, draws[target_index]))
                for k in pools:
                    hits[k][window].append(len(actual.intersection(ranked[:k])))
                    ties[k][window] += int(scores[ranked[k - 1]] == scores[ranked[k]])
            except Exception as exc:  # noqa: BLE001 -- collect scorer failures; main exits nonzero
                failures.append(
                    {
                        "game": game.key,
                        "method": name,
                        "target_index": target_index,
                        "error": repr(exc),
                    }
                )
        timing.append(
            {
                "game": game.key,
                "method": name,
                "seconds": time.perf_counter() - started,
                "flat_skipped": sum(flat),
            }
        )
        for k in pools:
            for target in targets:
                baseline = expected_random_rate(game.max_num, game.draw_n, k, target)
                for window in range(windows + 1):
                    aggregate = window == windows
                    observed = (
                        [hit for group in hits[k] for hit in group]
                        if aggregate
                        else hits[k][window]
                    )
                    n = len(observed)
                    successes = sum(h >= target for h in observed)
                    n_requested = count if aggregate else window_size
                    rows.append(
                        {
                            "game": game.key,
                            "method": name,
                            "pool": k,
                            "target": target,
                            "window": "all" if aggregate else str(window + 1),
                            "n_requested": n_requested,
                            "n_eval": n,
                            "successes": successes,
                            "rate": successes / n if n else None,
                            "random_expected": baseline,
                            "random_expected_successes": baseline * n,
                            "tie_fraction": (
                                (sum(ties[k]) if aggregate else ties[k][window]) / n
                                if n
                                else None
                            ),
                            "production_allowed": name not in EXCLUDED_FROM_PRODUCTION,
                            # Skipped observations must not produce a cherry-picked significance claim.
                            "p_raw": (
                                float(
                                    binomtest(
                                        successes, n, baseline, alternative="greater"
                                    ).pvalue
                                )
                                if aggregate and n == n_requested
                                else None
                            ),
                        }
                    )
    if history_before != digest(Path(game.csv_path)):
        raise RuntimeError(f"History changed during audit: {game.csv_path}")
    return {
        "game": game.key,
        "history_rows": len(draws),
        "first_target_index": start,
        "last_target_index": len(draws) - 1,
        "history_sha256": history_before,
        "rows": rows,
        "failures": failures,
        "timing": timing,
    }


def write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "bench_results/method_audit_20260921"
    )
    parser.add_argument("--window-size", type=int, default=60)
    parser.add_argument("--windows", type=int, default=3)
    args = parser.parse_args()
    if args.window_size < 1 or args.windows < 2:
        parser.error("Use a positive window size and at least two windows")
    args.output = args.output.resolve()
    # This script owns only its diagnostic directory, never production filenames.
    if args.output == ROOT or args.output == ROOT / "bench_results":
        parser.error("Choose a separate diagnostic subdirectory")
    args.output.mkdir(parents=True, exist_ok=True)
    before = {p: digest(ROOT / p) for p in PROTECTED}
    started = time.perf_counter()
    results = []
    with ProcessPoolExecutor(max_workers=4) as executor:
        pending = [
            executor.submit(audit_game, game, args.window_size, args.windows)
            for game in discover_games(str(ROOT / "_ISTORIC"))
        ]
        for future in as_completed(pending):
            result = future.result()
            results.append(result)
            print(
                f"Completed {result['game']}: {len(result['failures'])} errors",
                flush=True,
            )
    results.sort(key=lambda r: r["game"])
    rows = [row for r in results for row in r["rows"]]
    # Keep the planned family even when scorers fail or return flat scores.
    family = [row for row in rows if row["window"] == "all"]
    tested = [row for row in family if row["p_raw"] is not None]
    adjusted = holm_adjust(
        [row["p_raw"] if row["p_raw"] is not None else 1.0 for row in family]
    )
    for row in rows:
        row["p_holm"] = None
    for row, p in zip(family, adjusted):
        row["p_holm"] = p
    after = {p: digest(ROOT / p) for p in PROTECTED}
    summary = {
        "scope": "Retrospective diagnostic, not an untouched holdout or prize/ROI forecast",
        "methods": len(METHODS),
        "windows": args.windows,
        "window_size": args.window_size,
        "pools_main": [6, 8, 10, 12, 16],
        "pool_urna2": 1,
        "seconds": time.perf_counter() - started,
        "method_draw_evaluations_requested": len(METHODS)
        * len(results)
        * args.windows
        * args.window_size,
        "full_sample_comparisons": len(tested),
        "planned_comparisons": len(family),
        "significant_raw": sum(row["p_raw"] < 0.05 for row in tested),
        "significant_holm": sum(p < 0.05 for p in adjusted),
        "min_p_holm": min(adjusted, default=None),
        "failures": [error for result in results for error in result["failures"]],
        "games": [
            {
                key: value
                for key, value in result.items()
                if key not in {"rows", "failures", "timing"}
            }
            for result in results
        ],
        "protected_before": before,
        "protected_after": after,
        "production_unchanged": before == after,
    }
    write_csv(args.output / "rates.csv", rows)
    write_csv(
        args.output / "timing.csv",
        [row for result in results for row in result["timing"]],
    )
    (args.output / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                k: v
                for k, v in summary.items()
                if k not in {"games", "protected_before", "protected_after", "failures"}
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    if summary["failures"] or not summary["production_unchanged"]:
        raise SystemExit(
            "Method errors or concurrent production-state changes; inspect summary.json"
        )


if __name__ == "__main__":
    main()
