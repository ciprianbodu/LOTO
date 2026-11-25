"""Compare the pool winner and actual paid-ticket winner on the same Re-Bench origins."""
import argparse
from hashlib import sha256
from itertools import product
import json
import os
from pathlib import Path

import pandas as pd

from covering.lr_budget import ANY_PRIZE, FOUR_PLUS, recommend_games
from loto_enterprise.benchmark.lr_hypotheses import benchmark_lr_hypotheses
from loto_enterprise.core.lr_budget_forecast import budget_plan_scheme


def comparisons(history_dir, rows, budget):
    result = []
    for game, filename in (("6/49", "loto_6_49.csv"), ("5/40", "loto_5_40.csv"), ("joker", "joker.csv")):
        frame = pd.read_csv(history_dir / filename).tail(rows).reset_index(drop=True)
        fingerprint = sha256(frame.to_csv(index=False).encode()).hexdigest()
        for objective, inverse, restricted in product((FOUR_PLUS, ANY_PRIZE), (False, True), (False, True)):
            plan, = recommend_games(budget, objective, game)
            scheme = budget_plan_scheme(plan)
            report = benchmark_lr_hypotheses(
                game, (filename, frame), scheme=scheme.code, inverse=inverse, restricted=restricted,
                dynamic=False, lookback=100, hit_target=4, joker_size=3, budget_lei=budget,
                objective=objective,
            )
            portfolio = report.get("portfolio") or {}
            record = dict(game=game, rows=len(frame), history=fingerprint, budget=budget, scheme=scheme.code,
                          objective=objective, inverse=inverse, restricted=restricted)
            for field in ("error", "days", "draws", "pool_choice", "ticket_choice", "changed"):
                if field in portfolio:
                    record[field] = portfolio[field]
            if "changed" in portfolio:
                for label in ("pool_choice", "ticket_choice"):
                    recipe = portfolio[label]
                    score = next(c for c in portfolio["candidates"] if [c["main"], c.get("joker") or ""] == [n or "" for n in recipe])
                    record[label + "_score"] = {key: score.get(key, 0) for key in (
                        "n_eval", "infeasible", "hit4", "hit5", "hit6", "any_prize", "cost_bani")}
            elif "error" not in record:
                record["error"] = report.get("error") or "No portfolio decision"
            result.append(record)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("history", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--rows", type=int, default=180)
    parser.add_argument("--budget", type=int, default=200)
    args = parser.parse_args()
    if not os.environ.get("LOTO_RUNTIME_DIR") or not os.environ.get("LOTO_WF_CACHE_DIR"):
        parser.error("Set isolated runtime and cache directories")
    args.output.write_text(json.dumps(comparisons(args.history, args.rows, args.budget), indent=2), encoding="utf8")
