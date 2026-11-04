"""Benchmark exact profiles against the frozen previous NumPy implementation.

Run with python -m scripts.analysis.probability_opt_2026_11_04 --repeats 11.
This bypasses the result LRU, warms NumPy before timing, checks exact equality,
and measures one function; it does not estimate end-to-end application speed.
"""

from __future__ import annotations

import argparse
import json
import platform
import random
import statistics
import time
import tracemalloc
from pathlib import Path

import numpy as np

from covering.probability import _hit_counts


def previous_counts(v, masks):
    """Unchanged NumPy implementation before the 2026-11-04 optimization."""
    size = 1 << v
    family = np.zeros(size, dtype=bool)
    family[np.fromiter(masks, dtype=np.int64, count=len(masks))] = True
    for i in range(v):
        view = family.reshape(-1, 2, 1 << i)
        view[:, 0, :] |= view[:, 1, :]
    popcount = np.bitwise_count(np.arange(size, dtype=np.uint32)).astype(np.int8)
    best = np.where(family, popcount, 0).astype(np.int8)
    for i in range(v):
        view = best.reshape(-1, 2, 1 << i)
        np.maximum(view[:, 1, :], view[:, 0, :], out=view[:, 1, :])
    hist = np.bincount(
        popcount.astype(np.int64) * (v + 1) + best.astype(np.int64),
        minlength=(v + 1) * (v + 1),
    ).reshape(v + 1, v + 1)
    cumulative = hist[:, ::-1].cumsum(axis=1)[:, ::-1]
    return tuple(tuple(int(x) for x in row) for row in cumulative)


def _peak(fn, v, masks):
    tracemalloc.start()
    try:
        fn(v, masks)
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=11)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    after = _hit_counts.__wrapped__
    rows = []
    for v in (9, 12, 16, 19, 20):
        for kind in ("empty", "single", "many"):
            rng = random.Random(20261104 + v)
            n = {"empty": 0, "single": 1, "many": 100}[kind]
            masks = tuple(sorted({
                sum(1 << x for x in rng.sample(range(v), 5)) for _ in range(n)
            }))
            assert previous_counts(v, masks) == after(v, masks)
            samples = [[], []]
            functions = (previous_counts, after)
            # Alternate execution order to reduce drift from other CPU tasks.
            for repeat in range(args.repeats):
                for index in ((0, 1) if repeat % 2 else (1, 0)):
                    started = time.perf_counter()
                    functions[index](v, masks)
                    samples[index].append(time.perf_counter() - started)
            old, new = [statistics.median(x) * 1000 for x in samples]
            rows.append({
                "pool": v, "kind": kind, "tickets": len(masks),
                "before_ms": old, "after_ms": new, "speedup": old / new,
                "before_peak_bytes": _peak(previous_counts, v, masks),
                "after_peak_bytes": _peak(after, v, masks),
                "exact_equality": True,
            })
    result = {
        "python": platform.python_version(), "numpy": np.__version__,
        "repeats": args.repeats, "result_cache_bypassed": True,
        "scope": "single exact-profile function; timings depend on CPU load",
        "results": rows,
    }
    output = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(output, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
