"""Read-only cold-profile benchmark, with the previous implementation frozen.

Run from the repository root:
    python -m scripts.analysis.bench_probability_py314 --repeats 7

Measures a single exact profile calculation on each pool size, bypassing the
shared LRU in both versions. NumPy is imported before timing, as in the running
application. This is a function benchmark, not an end-to-end application speedup.
"""

from __future__ import annotations

import argparse
import json
import platform
import random
import statistics
import time
import tracemalloc

import numpy as np

from covering.probability import _hit_counts


def _previous_counts(v: int, masks: tuple[int, ...]):
    """The Python loop used on all pools of at most 16 before this change."""
    best = bytearray(1 << v)
    for ticket in masks:
        sub = ticket
        while sub:
            best[sub] = sub.bit_count()
            sub = (sub - 1) & ticket
    for mask in range(1, len(best)):
        if best[mask] == mask.bit_count():
            continue
        bits = mask
        while bits:
            bit = bits & -bits
            best[mask] = max(best[mask], best[mask ^ bit])
            bits ^= bit
    counts = [[0] * (v + 1) for _ in range(v + 1)]
    for mask, hits in enumerate(best):
        counts[mask.bit_count()][hits] += 1
    for row in counts:
        for threshold in range(v - 1, -1, -1):
            row[threshold] += row[threshold + 1]
    return tuple(tuple(row) for row in counts)


def _measure(fn, v, masks, repeats):
    times = []
    for _ in range(repeats):
        start = time.perf_counter()
        fn(v, masks)
        times.append(time.perf_counter() - start)
    tracemalloc.start()
    try:
        fn(v, masks)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return statistics.median(times) * 1000, peak


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=7)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    results = []
    for v in (6, 8, 9, 10, 12, 14, 16):
        rng = random.Random(314 + v)
        masks = tuple(sorted({
            sum(1 << n for n in rng.sample(range(v), 5)) for _ in range(80)
        }))
        assert _previous_counts(v, masks) == _hit_counts.__wrapped__(v, masks)
        before_ms, before_peak = _measure(_previous_counts, v, masks, args.repeats)
        after_ms, after_peak = _measure(_hit_counts.__wrapped__, v, masks, args.repeats)
        results.append({
            "pool": v,
            "tickets": len(masks),
            "before_ms": before_ms,
            "after_ms": after_ms,
            "speedup": before_ms / after_ms,
            "before_peak_bytes": before_peak,
            "after_peak_bytes": after_peak,
        })
    print(json.dumps({
        "python": platform.python_version(),
        "numpy": np.__version__,
        "repeats": args.repeats,
        "results": results,
    }, indent=2))


if __name__ == "__main__":
    main()
