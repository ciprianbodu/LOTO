"""Masoara numai completarea campurilor cache WF, fara datele utilizatorului.

Din radacina proiectului:
python -m scripts.analysis.benchmark_py314_wf_backfill --draws 100 --variants 930

Baseline-ul reproduce implementarea anterioara. Rezultatul nu este timpul
intregului walk-forward: masuram o etapa a incarcarii cache-ului deja calculat.
"""

from __future__ import annotations

import argparse
from dataclasses import MISSING, fields, is_dataclass
import gc
import json
import pickle
import statistics
import sys
import time

from loto_enterprise.core.walk_forward_adapter import (
    WalkForwardResult,
    _backfill_new_fields,
)


def _baseline(records):
    for obj in records or ():
        if not is_dataclass(obj):
            continue
        for fld in fields(obj):
            if hasattr(obj, fld.name):
                continue
            if fld.default is not MISSING:
                setattr(obj, fld.name, fld.default)
            elif fld.default_factory is not MISSING:
                setattr(obj, fld.name, fld.default_factory())
            else:
                setattr(obj, fld.name, None)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draws", type=int, default=100)
    parser.add_argument("--variants", type=int, default=930)
    parser.add_argument("--repeats", type=int, default=9)
    args = parser.parse_args()
    if min(args.draws, args.variants, args.repeats) < 1:
        parser.error("draws, variants si repeats trebuie sa fie pozitive")

    rows = []
    for draw in range(args.draws):
        context = {
            "pool": list(range(1, 20)),
            "scores": {n: n / 40 for n in range(1, 41)},
        }
        for variant in range(args.variants):
            rows.append(
                WalkForwardResult(
                    draw,
                    "2026-10-30",
                    [(variant + n) % 40 + 1 for n in range(5)],
                    variant % 5,
                    4,
                    wheel_coverage=100.0,
                    ticket_context=context,
                )
            )

    serialized_before = pickle.dumps(rows, protocol=pickle.HIGHEST_PROTOCOL)
    samples = {"baseline": [], "optimized": []}
    functions = [("baseline", _baseline), ("optimized", _backfill_new_fields)]
    for _, function in functions:
        function(rows)
    for repeat in range(args.repeats):
        for name, function in functions[:: 1 if repeat % 2 else -1]:
            gc.collect()
            start = time.perf_counter()
            function(rows)
            samples[name].append(1000 * (time.perf_counter() - start))
    identical = (
        pickle.dumps(rows, protocol=pickle.HIGHEST_PROTOCOL) == serialized_before
    )
    if not identical:
        raise AssertionError("Completarea a modificat inregistrarile curente")
    medians = {name: statistics.median(times) for name, times in samples.items()}
    print(
        json.dumps(
            {
                "python": sys.version,
                "rows": len(rows),
                "draws": args.draws,
                "variants": args.variants,
                "repeats": args.repeats,
                "median_ms": medians,
                "speedup": medians["baseline"] / medians["optimized"],
                "reduction_pct": 100 * (1 - medians["optimized"] / medians["baseline"]),
                "results_identical": identical,
                "samples_ms": samples,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
