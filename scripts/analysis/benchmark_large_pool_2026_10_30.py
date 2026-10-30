"""Reproducible, history-free full-universe wheel timings.

From the repository root:
    python -m scripts.analysis.benchmark_large_pool_2026_10_30
    python -m scripts.analysis.benchmark_large_pool_2026_10_30 --memory

The optional tracemalloc pass measures Python allocations and changes timings;
it does not measure all native allocations. Every case clears the position
cache, so the printed times are cold construction, not a cached WF replay.
"""

from __future__ import annotations

import argparse
import json
import platform
import time
import tracemalloc

from covering.large_pool import _canonical_cover, generate_large_pool_wheel


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--memory", action="store_true")
    args = parser.parse_args()
    print(json.dumps({"python": platform.python_version(), "platform": platform.platform()}))
    cases = [(v, pick, cap, None) for v, pick in [(49, 6), (40, 5), (45, 5)]
             for cap in [112, 10_000, 0]]
    cases.append((49, 6, 112, 6))
    for v, pick, cap, condition in cases:
        _canonical_cover.cache_clear()
        if args.memory:
            tracemalloc.start()
        start = time.perf_counter()
        wheel, coverage = generate_large_pool_wheel(range(1, v + 1), pick, 4, cap,
                                                   condition=condition)
        row = {"pool": v, "pick": pick, "guarantee": 4, "condition": condition,
               "budget": cap, "tickets": len(wheel), "coverage_pct": coverage,
               "represented_pool": len({n for t in wheel for n in t}),
               "seconds": round(time.perf_counter() - start, 6)}
        if args.memory:
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            row["peak_python_mib"] = round(peak / (1024 * 1024), 3)
        print(json.dumps(row), flush=True)
    for pick, guarantee in [(6, 6), (6, 5)]:
        try:
            generate_large_pool_wheel(range(1, 50), pick, guarantee)
        except ValueError as exc:
            print(json.dumps({"pool": 49, "pick": pick, "guarantee": guarantee,
                              "budget": 0, "guard": str(exc)}))
        else:
            raise AssertionError("Huge unlimited-system guard did not run")


if __name__ == "__main__":
    main()
