"""Same-input CPU timings for generation, Re-Bench and paid-ticket holdout replay.

Run with an isolated runtime and the same CSV input on each revision. The optional
legacy mode measures the pool-only Re-Bench that existed before budget was an input.
"""

import argparse
import hashlib
import inspect
import json
import os
from pathlib import Path
import platform
import sys
import threading
import time

import pandas as pd
import psutil


def measure(fn):
    process = psutil.Process()
    stop = threading.Event()
    samples = [process.memory_info().rss]
    def watch():
        while not stop.wait(0.01):
            samples.append(process.memory_info().rss)
    observer = threading.Thread(target=watch, daemon=True)
    observer.start()
    start = time.perf_counter()
    try:
        result = fn()
    finally:
        elapsed = time.perf_counter() - start
        stop.set()
        observer.join()
    samples.append(process.memory_info().rss)
    return result, {"seconds": round(elapsed, 4), "rss_peak_mib": round(max(samples) / 2**20, 2)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("csv", type=Path)
    ap.add_argument("output", type=Path)
    ap.add_argument("--rows", type=int, default=110)
    ap.add_argument("--game", default="6/49", choices=("6/49", "5/40", "joker"))
    ap.add_argument("--inverse", action="store_true")
    ap.add_argument("--restricted", action="store_true")
    args = ap.parse_args()
    sys.path.insert(0, str(args.root.resolve()))
    from covering.lr_budget import FOUR_PLUS, recommend_games
    from loto_enterprise.benchmark.lr_hypotheses import benchmark_lr_hypotheses
    from loto_enterprise.core.lr_budget_forecast import budget_plan_scheme, recommend_playable_games
    from loto_enterprise.core.lr_range_analysis import RANGES
    from loto_enterprise.core.lr_walk_forward import validate_lr_walk_forward
    if not os.environ.get("LOTO_RUNTIME_DIR") or not os.environ.get("LOTO_WF_CACHE_DIR"):
        raise ValueError("Set isolated runtime and cache paths.")
    game = args.game
    frame = pd.read_csv(args.csv).tail(args.rows).reset_index(drop=True)
    source = args.csv.name, frame
    budget = 100
    plan, = recommend_games(budget, FOUR_PLUS, game)
    scheme = budget_plan_scheme(plan).code
    options = dict(scheme=scheme, inverse=args.inverse, restricted=args.restricted, dynamic=False,
                   lookback=100, hit_target=4, joker_size=3)
    supports_budget = "budget_lei" in inspect.signature(benchmark_lr_hypotheses).parameters
    scenarios = {
        "generate": lambda: recommend_playable_games(budget, FOUR_PLUS, game, {game: source},
                    inverse=args.inverse, number_range=RANGES[1] if args.restricted else None,
                    pool_codes={game: scheme}, dynamic=False, hit_target=4, joker_size=3)[0],
        "rebench": lambda: benchmark_lr_hypotheses(game, source, **options, **(
                    {"budget_lei": budget, "objective": FOUR_PLUS} if supports_budget else {})),
        "replay": lambda: validate_lr_walk_forward(game, source, budget_lei=budget, objective=FOUR_PLUS,
                                                  depth=10, **options),
    }
    report = {"python": platform.python_version(), "system": platform.platform(), "game": game,
              "rows": len(frame), "csv_sha256": hashlib.sha256(frame.to_csv(index=False).encode()).hexdigest(),
              "options": options, "restricted_range": RANGES[1], "budget_lei": budget, "timings": {}}
    for name, fn in scenarios.items():
        runs = []
        for label in ("first", "repeat"):
            result, record = measure(fn)
            if name == "generate":
                if not result.get("tickets"):
                    raise ValueError(str(result.get("forecast_error")))
                record.update(price_bani=result["price_bani"], variants=result["variants"],
                              pool=list(result["forecast"]["pool"]))
            elif name == "rebench":
                record["methods"] = [u["method"] for u in result["urns"]]
                record["portfolio"] = result.get("portfolio")
            else:
                record["summary"] = result["summary"]
            runs.append(dict(run=label, **record))
        report["timings"][name] = runs
    args.output.write_text(json.dumps(report, indent=2, default=str), encoding="utf8")


if __name__ == "__main__":
    main()
