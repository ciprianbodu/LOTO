"""Compare compact LR grouping and payouts against an explicit source baseline.

Both versions group exact intersections. Histories, prices and scheme data are
never modified. The deterministic synthetic draws include four to six pool
hits, as in the visible financial history. Run in an isolated runtime.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
from random import Random
from statistics import median
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from covering import lr_schemes as current
from loto_enterprise.core.generated_prize import estimate_generated_prize
from loto_enterprise.core.official_prizes import PrizeCategory, PrizeDraw

CASES = (("6/49", 16, 59, 49, 6), ("joker", 15, 12, 45, 5), ("5/40", 19, 48, 40, 6))
LABELS = {
    "6/49": ("6/6", "5/6", "4/6", "3/6"),
    "5/40": ("5/5", "5/6", "4/6"),
    "joker": ("5/5+J", "5/5", "4/5+J", "4/5", "3/5+J", "3/5", "2/5+J", "1/5+J"),
}
NAMES = ("I", "II", "III", "IV", "V", "VI", "VII", "VIII")


def time_pair(before, after, repeats):
    times = [[], []]
    outputs = [None, None]
    for repeat in range(repeats):
        for which in ((0, 1) if repeat % 2 == 0 else (1, 0)):
            started = perf_counter()
            outputs[which] = (before, after)[which]()
            times[which].append(perf_counter() - started)
        if outputs[0] != outputs[1]:
            raise AssertionError("Baseline and current outputs differ")
    old, new = (median(values) for values in times)
    return {
        "old_seconds": old,
        "new_seconds": new,
        "speedup": old / new,
        "repeats": repeats,
        "identical_outputs": True,
    }


def run(baseline_source, *, repeats=7, group_rows=1200, prize_rows=120):
    spec = importlib.util.spec_from_file_location("lr_groups_baseline", baseline_source)
    old = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = old
    spec.loader.exec_module(old)
    rng = Random(20261105)
    results = []
    for game, n, code, maximum, draw_n in CASES:
        scheme = next(s for s in current.lr_schemes(game, n) if s.code == code)
        prior = old.LRScheme(scheme.game, scheme.code, scheme.n, scheme.variants)
        categories = tuple(
            PrizeCategory(name, label, 1, (i + 1) * 100, None, (i + 1) * 100)
            for i, (name, label) in enumerate(zip(NAMES, LABELS[game]))
        )
        financial = []
        for index in range(max(group_rows, prize_rows)):
            pool = rng.sample(range(1, maximum + 1), n)
            count = 4 + index % (draw_n - 3)
            outside = sorted(set(range(1, maximum + 1)) - set(pool))
            drawn = rng.sample(pool, count) + rng.sample(outside, draw_n - count)
            rng.shuffle(drawn)
            official = PrizeDraw(
                game, "2026-11-01", tuple(drawn), 7 if game == "joker" else None,
                categories, "https://www.loto.ro/", order_known=True,
            )
            financial.append((pool, drawn, official))

        def cold(module):
            fresh = module.LRScheme(scheme.game, scheme.code, scheme.n, scheme.variants)
            pool, drawn, _ = financial[0]
            return module.scheme_prize_groups(fresh, pool, drawn, joker=7)

        cold_times = time_pair(lambda: cold(old), lambda: cold(current), repeats)

        def groups(module, instance):
            return [
                module.scheme_prize_groups(instance, p, d, joker=7)
                for p, d, _ in financial[:group_rows]
            ]

        # Warm both instances before the steady-state measurement.
        if groups(old, prior) != groups(current, scheme):
            raise AssertionError("Initial compact groups differ")
        group_times = time_pair(
            lambda: groups(old, prior), lambda: groups(current, scheme), repeats
        )

        def prizes(module, instance):
            values = []
            for pool, drawn, official in financial[:prize_rows]:
                grouped = module.scheme_prize_groups(instance, pool, drawn, joker=7)
                values.append(estimate_generated_prize(
                    game, [], official, variant_prize_groups=grouped,
                    target_numbers=drawn, target_date=official.draw_date,
                    expected_count=scheme.size, pool_numbers=pool,
                ))
            return values

        prize_times = time_pair(
            lambda: prizes(old, prior), lambda: prizes(current, scheme), repeats
        )
        row = {
            "game": game, "pool": n, "code": code, "variants": scheme.size,
            "cold_single_group": cold_times,
            "warm_groups": {"rows": group_rows, **group_times},
            "warm_prizes": {"rows": prize_rows, **prize_times},
        }
        results.append(row)
        print(json.dumps(row), flush=True)
    return {
        "python": sys.version,
        "seed": 20261105,
        "baseline_source_sha256": hashlib.sha256(
            baseline_source.read_bytes()
        ).hexdigest(),
        "current_source_sha256": hashlib.sha256(
            Path(current.__file__).read_bytes()
        ).hexdigest(),
        "data": (
            "Synthetic repeatable pools, draws and prices; "
            "official scheme rows retained."
        ),
        "scope": (
            "Compact grouping and validated payout functions, "
            "not whole application runtime."
        ),
        "cases": results,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--group-rows", type=int, default=1200)
    parser.add_argument("--prize-rows", type=int, default=120)
    args = parser.parse_args()
    if min(args.repeats, args.group_rows, args.prize_rows) < 1:
        parser.error("repeats and row counts must be positive")
    result = run(
        args.baseline_source, repeats=args.repeats,
        group_rows=args.group_rows, prize_rows=args.prize_rows,
    )
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
