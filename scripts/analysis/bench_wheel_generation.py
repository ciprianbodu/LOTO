"""Compare deterministic wheel generation against a Git baseline, read-only.

Run from the repository: python scripts/analysis/bench_wheel_generation.py
No benchmark decisions, historical data, or runtime caches are modified.
"""

from __future__ import annotations

import argparse
import ast
import json
import logging
from pathlib import Path
import statistics
import subprocess
import sys
import time
import tracemalloc
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import loto_engine as engine
import wheeling_methods as wm


def baseline_function(ref, module, name):
    """Load just the old function, using its unchanged module dependencies."""
    filename = Path(module.__file__).name
    source = subprocess.check_output(
        ["git", "show", f"{ref}:{filename}"], cwd=ROOT, text=True, encoding="utf-8"
    )
    node = next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    namespace = vars(module).copy()
    exec(compile(ast.Module(body=[node], type_ignores=[]), filename, "exec"), namespace)
    return namespace[name]


def measure(fn, repair, args, repeats):
    with patch.object(wm, "ensure_pool_numbers_on_tickets", repair):
        result = fn(*args)  # warm-up
        durations = []
        for _ in range(repeats):
            start = time.perf_counter()
            assert fn(*args) == result
            durations.append(time.perf_counter() - start)
        tracemalloc.start()
        try:
            fn(*args)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
    return result, statistics.median(durations) * 1000, peak


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default="67effec")
    parser.add_argument("--repeats", type=int, default=9)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    logging.disable(logging.CRITICAL)
    old = baseline_function(args.baseline, engine, "generate_combinatorial_wheel")
    old_repair = baseline_function(args.baseline, wm, "ensure_pool_numbers_on_tickets")
    new_repair = wm.ensure_pool_numbers_on_tickets
    rows = []
    for v, pick, guarantee, budget in [
        (11, 5, 3, 0),
        (16, 5, 4, 0),
        (16, 6, 4, 0),
        (16, 6, 6, 7),
    ]:
        pool = list(range(1, v + 1))
        scores = {n: (n * 17) % 23 for n in pool}
        call_args = (pool, pick, guarantee, budget, scores)
        before, old_ms, old_peak = measure(old, old_repair, call_args, args.repeats)
        after, new_ms, new_peak = measure(
            engine.generate_combinatorial_wheel, new_repair, call_args, args.repeats
        )
        assert before == after, "Ticket order or coverage changed"
        rows.append(
            dict(
                pool=v,
                pick=pick,
                guarantee=guarantee,
                budget=budget,
                tickets=len(after[0]),
                coverage=after[1],
                before_ms=round(old_ms, 3),
                after_ms=round(new_ms, 3),
                speedup=round(old_ms / new_ms, 2),
                before_peak_bytes=old_peak,
                after_peak_bytes=new_peak,
                identical=True,
            )
        )
    print(
        json.dumps(
            dict(
                python=sys.version,
                baseline=args.baseline,
                repeats=args.repeats,
                rows=rows,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
