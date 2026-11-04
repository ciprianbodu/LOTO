"""Measure certified pool-20 improvements against an explicit old source file.

Example (extract the original source from commit
5b5624050e8c66625b032b12c3fb4f6c646d9379 into a temporary file first):

python scripts/analysis/bench_large_pool_certified.py --baseline-source OLD.py \
    --output scripts/analysis/large_pool_certified_2026-11-04.json

No draw histories, production state, network or mutable decision files are read.
Wall-clock results depend on machine load; exact integer profiles do not.
"""
from __future__ import annotations

import argparse
import importlib.util
import hashlib
import json
import math
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from covering import large_pool as current
from covering.probability import wheel_hit_profile


def probability(profile, threshold, draw_n, max_num, v=20):
    numerator = sum(
        profile[size][threshold] * math.comb(max_num - v, draw_n - size)
        for size in range(min(draw_n, v) + 1)
        if 0 <= draw_n - size <= max_num - v
    )
    return numerator, math.comb(max_num, draw_n)


def run(baseline_path, repeats=1):
    baseline_sha256 = hashlib.sha256(baseline_path.read_bytes()).hexdigest()
    spec = importlib.util.spec_from_file_location(
        "large_pool_before_20261104", baseline_path
    )
    old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)
    rows = []
    caps = (10, 32, 64, 100, 125, 152, 200, 250, 300, 404, 500, 750)
    for pick in (6, 5):
        for cap in caps:
            times = [[], []]
            wheels = [None, None]
            for repeat in range(repeats):
                # Alternate the order to reduce systematic timing-order bias.
                for index in ((0, 1) if repeat % 2 == 0 else (1, 0)):
                    module = (old, current)[index]
                    module._canonical_cover.cache_clear()
                    started = time.perf_counter()
                    wheels[index] = module._canonical_cover(20, pick, 4, cap)
                    times[index].append(time.perf_counter() - started)
            before, after = (
                wheel_hit_profile(tuple(range(20)), wheel) for wheel in wheels
            )
            assert len(wheels[0]) == len(wheels[1])
            for size in range(3, 7):
                for target in range(3, min(size, pick) + 1):
                    assert after[size][target] >= before[size][target]
            geometry = []
            games = (
                (("6/49", 6, 49),) if pick == 6
                else (("5/40", 6, 40), ("Joker Urna 1", 5, 45))
            )
            for name, draw_n, max_num in games:
                values = {}
                for target in range(3, 7):
                    a, denominator = probability(before, target, draw_n, max_num)
                    b, _ = probability(after, target, draw_n, max_num)
                    assert b >= a
                    values[str(target)] = {
                        "before_favourable_draws": a,
                        "after_favourable_draws": b,
                        "total_draws": denominator,
                        "before_probability": a / denominator,
                        "after_probability": b / denominator,
                        "relative_gain_percent": (b / a - 1) * 100 if a else None,
                    }
                geometry.append({"game": name, "hits": values})
            row = {
                "pool": 20, "pick": pick, "guarantee": 4, "cap": cap,
                "tickets": len(wheels[0]), "changed": wheels[0] != wheels[1],
                "before_covered_g": before[4][4], "after_covered_g": after[4][4],
                "old_seconds": statistics.median(times[0]),
                "new_seconds": statistics.median(times[1]),
                "timing_repeats": repeats, "games": geometry,
            }
            rows.append(row)
            print(
                f"pick={pick} cap={cap}: changed={row['changed']}, "
                f"old={row['old_seconds']:.4f}s, new={row['new_seconds']:.4f}s",
                flush=True,
            )
    return {
        "baseline_commit_expected": "5b5624050e8c66625b032b12c3fb4f6c646d9379",
        "baseline_source_sha256": baseline_sha256,
        "source_identity_note": (
            "Caller supplies the old source extracted from the expected commit; "
            "the byte hash identifies the actual file measured."
        ),
        "description": (
            "Exact all-outcome probabilities; no historical fitting; timing "
            "includes full construction and final certification."
        ),
        "python": sys.version, "cases": rows,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=1)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    result = run(args.baseline_source, args.repeats)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
