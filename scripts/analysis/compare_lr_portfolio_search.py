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
from unittest.mock import patch

import numpy as np

from covering import lr_pool_budget as production
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


def measured_stages(plan, objective, pool, jokers):
    """Observe real optimizer checkpoints without spending its time on grading."""
    marks = {}

    class TracedSearch(production._PortfolioSearch):
        def checkpoint(self, stage):
            marks[stage] = self, self.best, perf_counter() - start, self.catalogue_optimization

        def optimize(self, incumbent, known_value):
            selected = super().optimize(incumbent, known_value)
            if "initial" not in marks:
                self.checkpoint("initial")
            return selected

        def search(self, incumbent):
            self.checkpoint("initial")
            return super().search(incumbent)

        def grow(self, frontier, candidates, beam_width, slice_end):
            if "beam" not in marks:
                phase = "beam"
            else:
                # The only grow using the full deadline is a beam pass over
                # all eligible placements when the MILP branch is skipped.
                phase = "wide_beam" if slice_end == self.deadline else "lns"
            try:
                return super().grow(frontier, candidates, beam_width, slice_end)
            finally:
                self.checkpoint(phase)

        def solve(self, candidates):
            try:
                return super().solve(candidates)
            finally:
                self.checkpoint("milp")

    # Record one normal production call, not independently retimed prefixes.
    # The incumbent and zone caches stay as in ordinary sequential usage.
    with patch.object(production, "_PortfolioSearch", TracedSearch):
        production._refine_cached.cache_clear()
        start = perf_counter()
        improved = restrict_to_pool(deepcopy(plan), objective, pool, jokers)
        elapsed = perf_counter() - start

    observations = {}
    previous = None
    for phase in ("initial", "beam", "lns", "milp", "wide_beam"):
        if phase in marks:
            search, chosen, seconds, local = marks[phase]
            zones = tuple(
                (c.zone.code, c.zone.positions, c.zone.variants, search.slot(i, c))
                for i, c in enumerate(chosen.choices)
            )
            candidate = production._attach(
                deepcopy(plan), search.lot, zones, tuple(sorted(pool)), jokers,
                chosen.price, chosen.score, search.upper,
            )
            verified = exact(candidate, pool, jokers)
            status = "observed" if phase != "milp" or local is not None else "no_milp_result"
            details = {
                "status": status,
                "elapsed_seconds": round(seconds, 5),
                **{key: verified[key] for key in ("attained", "4+", "5+", "6+", "cost_bani", "upper", "gap")},
                "global_optimal": verified["gap"] == 0,
                "candidate_milp": local if phase == "milp" else None,
            }
            if previous is not None:
                assert details["attained"] >= previous["attained"]
                assert details["elapsed_seconds"] >= previous["elapsed_seconds"]
            previous = details
        else:
            if phase == "initial":
                verified = exact(improved, pool, jokers)
                details = {
                    "status": "observed",
                    "elapsed_seconds": round(elapsed, 5),
                    **{key: verified[key] for key in ("attained", "4+", "5+", "6+", "cost_bani", "upper", "gap")},
                    "global_optimal": verified["gap"] == 0,
                    "candidate_milp": None,
                }
                previous = details
            else:
                cause = "global_bound" if previous["global_optimal"] else "dispatch_or_time"
                if phase == "lns" and cause != "global_bound" and marks:
                    sizes = [len(marks[key][1].choices) for key in ("initial", "beam") if key in marks]
                    if sizes and max(sizes) < 2:
                        cause = "fewer_than_two_zones"
                details = {"status": cause}
        observations[phase] = details
    assert previous["attained"] == improved["attained"]
    initial = observations["initial"]
    for checkpoint in observations.values():
        if "attained" in checkpoint:
            assert checkpoint["cost_bani"] <= initial["cost_bani"]
            assert all(checkpoint[key] >= initial[key] for key in ("4+", "5+", "6+"))
    return improved, elapsed, observations


def run(old, store, *, ablation=False):
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
                if ablation:
                    improved, elapsed, checkpoints = measured_stages(plan, objective, pool, jokers)
                else:
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
                if ablation:
                    assert checkpoints["initial"]["attained"] == a["attained"]
                    assert checkpoints["initial"]["cost_bani"] == a["cost_bani"]
                    item["ablation"] = checkpoints
                store(item)
                print(game, objective, label, a["attained"], b["attained"], item["refined_seconds"], flush=True)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--baseline-source", type=Path)
    cli.add_argument("--out", required=True, type=Path)
    cli.add_argument("--ablation", action="store_true", help="Recount the portfolio after each executed search stage.")
    args = cli.parse_args()
    rows = []
    def save(item):
        rows.append(item)
        args.out.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    run(old_version(args.baseline_source), save, ablation=args.ablation)


if __name__ == "__main__":
    main()
