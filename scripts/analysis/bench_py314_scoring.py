"""Reproducible microbenchmarks; old implementations are embedded as references.

Run from the project root: python -m scripts.analysis.bench_py314_scoring
Results concern these primitives/scorers, not total application runtime.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import tracemalloc
from pathlib import Path

import numpy as np

from loto_enterprise.benchmark import methods_common as common
from loto_enterprise.benchmark import methods_recency as recency


def reference_last_seen(ind):
    n = ind.shape[0]
    if n == 0:
        return np.full(ind.shape[1], -1, dtype=np.int64)
    indices = np.where(ind > 0, np.arange(n, dtype=np.int64)[:, None], -1)
    return indices.max(axis=0)


def reference_vector_to_scores(vec, max_num):
    v = np.asarray(vec, dtype=np.float64).reshape(-1)
    if v.shape[0] != int(max_num):
        full = np.zeros(int(max_num), dtype=np.float64)
        full[: min(v.shape[0], int(max_num))] = v[: int(max_num)]
        v = full
    finite = np.isfinite(v)
    if not finite.any():
        return {i + 1: 0.0 for i in range(int(max_num))}
    vmin = float(v[finite].min())
    vmax = float(v[finite].max())
    value_range = max(vmax - vmin, 1e-12)
    out = np.where(finite, (v - vmin) / value_range, 0.0)
    return {i + 1: float(out[i]) for i in range(int(max_num))}


def measure_pair(before, after, loops, rounds):
    timings = [[], []]
    for round_index in range(rounds):
        # Alternate the order to reduce systematic drift/CPU warm-up bias.
        for index in ([0, 1] if round_index % 2 == 0 else [1, 0]):
            function = (before, after)[index]
            start = time.perf_counter_ns()
            for _ in range(loops):
                function()
            timings[index].append((time.perf_counter_ns() - start) / loops / 1e3)
    peaks = []
    for function in (before, after):
        tracemalloc.start()
        function()
        peaks.append(tracemalloc.get_traced_memory()[1])
        tracemalloc.stop()
    median = [statistics.median(values) for values in timings]
    return {
        "before_us": median[0],
        "after_us": median[1],
        "speedup": median[0] / median[1],
        "peak_bytes_before": peaks[0],
        "peak_bytes_after": peaks[1],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, default=9)
    parser.add_argument("--loops", type=int, default=250)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    rng = np.random.default_rng(314)
    results = []
    for n, m in [(50, 49), (400, 49), (3000, 49), (3000, 40), (3000, 45), (3000, 20)]:
        ind = (rng.random((n, m)) < 6 / m).astype(np.float64)
        np.testing.assert_array_equal(reference_last_seen(ind), common.last_seen_index(ind))
        result = measure_pair(
            lambda: reference_last_seen(ind),
            lambda: common.last_seen_index(ind),
            args.loops,
            args.rounds,
        )
        results.append({"function": "last_seen_index", "shape": [n, m], **result})
    for m in (20, 40, 45, 49):
        vec = rng.normal(size=m)
        assert reference_vector_to_scores(vec, m) == common.vector_to_scores(vec, m)
        result = measure_pair(
            lambda: reference_vector_to_scores(vec, m),
            lambda: common.vector_to_scores(vec, m),
            args.loops,
            args.rounds,
        )
        results.append({"function": "vector_to_scores", "max_num": m, **result})
    original_last = recency.last_seen_index
    original_vector = recency.vector_to_scores
    # Real immutable CSV inputs; no benchmark decision, cache or history writes.
    for file_name, max_num, columns in (
        ("loto_6_49.csv", 49, [f"n{i}" for i in range(1, 7)]),
        ("loto_5_40.csv", 40, [f"n{i}" for i in range(1, 7)]),
        ("joker.csv", 45, [f"n{i}" for i in range(1, 6)]),
    ):
        import pandas as pd

        draws = pd.read_csv(Path("_ISTORIC") / file_name)[columns].to_numpy(dtype=np.int64)

        def before():
            recency.last_seen_index = reference_last_seen
            recency.vector_to_scores = reference_vector_to_scores
            try:
                return recency.score_gap_current(draws, max_num)
            finally:
                recency.last_seen_index = original_last
                recency.vector_to_scores = original_vector

        def after():
            return recency.score_gap_current(draws, max_num)

        assert before() == after()
        result = measure_pair(before, after, args.loops, args.rounds)
        results.append(
            {"function": "score_gap_current", "history": file_name, "rows": len(draws), **result}
        )
    report = {"python": sys.version, "numpy": np.__version__, "results": results}
    output = json.dumps(report, indent=2)
    print(output)
    if args.output:
        args.output.write_text(output + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
