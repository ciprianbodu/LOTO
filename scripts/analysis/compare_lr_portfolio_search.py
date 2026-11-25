"""Equal-cost baseline and exact outcome comparison of whole LR zones."""

import argparse
import importlib.util
import json
import sys
from copy import deepcopy
from itertools import combinations
from math import comb
from pathlib import Path
from time import perf_counter

import numpy as np

from covering.lr_budget import ANY_PRIZE, FOUR_PLUS, recommend_games
from covering.lr_pool_budget import restrict_to_pool
from covering.lr_schemes import lr_schemes
from loto_enterprise.core.lotteries import require_lottery

GAMES = ("6/49", "5/40", "joker")
POOLS = {
    "6/49": (2, 3, 6, 9, 10, 14, 15, 19, 23, 24, 28, 32, 33, 38, 43, 48),
    "5/40": (1, 2, 4, 7, 8, 10, 13, 14, 16, 19, 20, 23, 25, 26, 29, 30, 33, 36, 39),
    "joker": (2, 5, 8, 9, 12, 15, 16, 19, 22, 23, 27, 28, 32, 37, 43),
}
JOKERS = (3, 13)


def old_version(path):
    if not path:
        return lambda plan, objective, pool, jokers: restrict_to_pool(plan, objective, pool, jokers, time_limit_s=0)
    spec = importlib.util.spec_from_file_location("lr_pool_comparison_baseline", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.restrict_to_pool


def exact(row, pool, jokers):
    """Grade the actual individual variants over every attainable intersection."""
    game = row["game"]
    lot = require_lottery(game, "RO")
    scheme = {s.code: s for s in lr_schemes(game)}
    position = {n: i for i, n in enumerate(pool)}
    bets = []
    for bundle in row["tickets"]:
        assert 0 < len(bundle) <= lot.per_ticket
        for zone in bundle:
            balls, joker = zone["numbers"], zone["joker"]
            rules = scheme[zone["code"]]
            assert len(balls) == len(set(balls)) == rules.n
            assert balls == sorted(balls) and set(balls) <= set(pool)
            assert (joker in jokers) if lot.geo.second else (joker is None)
            assert len(set(rules.variants)) == rules.size
            for variant in rules.variants:
                bets.append((sum(1 << position[balls[i - 1]] for i in variant), joker))
    spent = round(lot.price * 100) * len(bets) + 50 * len(row["tickets"])
    assert len(bets) == row["variants"] and spent == row["price_bani"] <= row["budget_bani"]
    masks = []
    layers = []
    for k in range(min(lot.draw_n, len(pool)) + 1):
        outside = lot.draw_n - k
        if outside > lot.max_n - len(pool):
            continue
        layer = [sum(1 << j for j in subset) for subset in combinations(range(len(pool)), k)]
        masks.extend(layer)
        layers.extend([comb(lot.max_n - len(pool), outside)] * len(layer))
    weights = np.array(layers, dtype=np.int64)
    masks = np.array(masks, dtype=np.uint32)
    scores = np.zeros(len(masks), dtype=np.uint8)
    touches = {j: np.zeros(len(masks), dtype=bool) for j in jokers}
    for ticket, joker in set(bets):
        hits = np.bitwise_count(masks & ticket)
        np.maximum(scores, hits, out=scores)
        if joker is not None:
            touches[joker] |= hits > 0
    wins = {t: int(weights[scores >= t].sum()) for t in range(1, lot.draw_n + 1)}
    denominator = comb(lot.max_n, lot.draw_n)
    if lot.geo.second:
        urn = lot.geo.second.max_n
        prize = urn * wins[3] + sum(int(weights[touch & (scores < 3)].sum()) for touch in touches.values())
        prize_denominator = denominator * urn
    else:
        prize = wins[3 if game == "6/49" else 4]
        prize_denominator = denominator
    primary = prize if row["objective"] == ANY_PRIZE else wins[4]
    assert primary == row["attained"]
    assert row["denominator"] == (prize_denominator if row["objective"] == ANY_PRIZE else denominator)
    assert wins.get(6, 0) == (len({ticket for ticket, _ in bets}) if game == "6/49" else 0)
    return {
        "attained": primary,
        "4+": wins[4],
        "5+": wins[5],
        "6+": wins.get(6, 0),
        "denominator": denominator,
        "any_prize": prize,
        "prize_denominator": prize_denominator,
        "cost_bani": spent,
        "zones": sum(map(len, row["tickets"])),
        "physical_tickets": len(row["tickets"]),
        "duplicate_paid_variants": len(bets) - len(set(bets)),
        "upper": row["upper"],
        "gap": row["upper"] - primary,
        "codes": [zone["code"] for ticket in row["tickets"] for zone in ticket],
        "certificates": row.get("optimization"),
    }


def run(old, store):
    for game in GAMES:
        lot = require_lottery(game, "RO")
        jokers = JOKERS if lot.geo.second else ()
        cases = [(f"{lei} lei", lei * 100, POOLS[game]) for lei in (100, 200, 300, 400)]
        first = min(lr_schemes(game), key=lambda s: s.size)
        unit = round(lot.price * 100)
        edge = first.size * (lot.per_ticket + 1) * unit + 100
        # Restrict the pool around the minimum code, simultaneously testing
        # a second physical ticket fee and feasibility of whole added zones.
        cases.extend((f"taxă {offset:+}", edge + offset, POOLS[game][:first.n + 1]) for offset in (-1, 0, 1))
        for objective in (FOUR_PLUS, ANY_PRIZE):
            for label, budget, pool in cases:
                # Sub-leu rows need an exact-budget baseline template. Using
                # the next integral lei for the unrestricted template can
                # spend too much, so force the placement engine on these rows.
                plan, = recommend_games(max(100, (budget + 99) // 100), objective, game)
                if budget % 100:
                    plan["tickets"] = []
                    plan["price_bani"] = plan["attained"] = plan["variants"] = 0
                plan["budget_bani"] = budget
                start = perf_counter()
                reference = old(deepcopy(plan), objective, pool, jokers)
                previous = perf_counter() - start
                start = perf_counter()
                improved = restrict_to_pool(deepcopy(plan), objective, pool, jokers)
                elapsed = perf_counter() - start
                a, b = exact(reference, pool, jokers), exact(improved, pool, jokers)
                assert b["cost_bani"] <= a["cost_bani"]
                assert all(b[k] >= a[k] for k in ("attained", "4+", "5+", "6+"))
                item = {
                    "game": game,
                    "objective": objective,
                    "scenario": label,
                    "budget_bani": budget,
                    "pool": pool,
                    "jokers": jokers,
                    "baseline_seconds": round(previous, 5),
                    "refined_seconds": round(elapsed, 5),
                    "baseline": a,
                    "refined": b,
                    "delta": {
                        k: {
                            "numerator": b[k] - a[k],
                            "percentage_points": 100 * (b[k] - a[k]) /
                            (b["prize_denominator"] if k == "attained" and objective == ANY_PRIZE else b["denominator"]),
                            "relative_percent": 100 * (b[k] - a[k]) / a[k] if a[k] else None,
                        }
                        for k in ("attained", "4+", "5+", "6+")
                    },
                }
                store(item)
                print(game, objective, label, a["attained"], b["attained"], item["refined_seconds"], flush=True)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--baseline-source", type=Path)
    cli.add_argument("--out", required=True, type=Path)
    args = cli.parse_args()
    rows = []
    def save(item):
        rows.append(item)
        args.out.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    run(old_version(args.baseline_source), save)


if __name__ == "__main__":
    main()
