"""Equal-budget ticket experiments; no observed draw enters construction.

Uniform-draw probabilities describe ticket geometry, not forecasting skill.
The universe search is a bounded heuristic on a DESIGN sample. Probability
estimates use a separate, shared sample, never the search sample.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from math import comb, sqrt
import operator

import numpy as np

from loto_enterprise.core.generated_prize import estimate_generated_prize
from loto_enterprise.core.prize_categories import resolve_variant_category


@dataclass(frozen=True)
class Game:
    key: str
    maximum: int
    drawn: int
    pick: int
    joker: bool = False


GAMES = {
    "loto_6_49": Game("loto_6_49", 49, 6, 6),
    "loto_5_40": Game("loto_5_40", 40, 6, 5),
    "joker": Game("joker", 45, 5, 5, True),
}


def integer(value, *, minimum=0):
    if isinstance(value, (bool, np.bool_)):
        raise ValueError("boolean instead of integer")
    value = operator.index(value)
    if value < minimum:
        raise ValueError(f"integer must be >= {minimum}")
    return int(value)


def validate_tickets(game: Game, tickets) -> list[list[int]]:
    result, seen = [], set()
    for line in tickets:
        numbers = tuple(integer(n, minimum=1) for n in line)
        if len(numbers) != game.pick + int(game.joker):
            raise ValueError("incomplete simple variant")
        main = tuple(sorted(numbers[:game.pick]))
        if len(set(main)) != game.pick or max(main) > game.maximum:
            raise ValueError("invalid main numbers")
        if game.joker and numbers[-1] > 20:
            raise ValueError("invalid Joker ball")
        canonical = main + (numbers[-1:] if game.joker else ())
        if canonical in seen:
            raise ValueError("duplicate variant: budget must count distinct variants")
        seen.add(canonical)
        result.append(list(canonical))
    if not result:
        raise ValueError("no generated variants")
    return result


def random_tickets(game: Game, budget: int, seed: int, *, pool=None, joker=None):
    budget = integer(budget, minimum=1)
    numbers = list(range(1, game.maximum + 1)) if pool is None else list(pool)
    if len(set(numbers)) != len(numbers) or any(n < 1 or n > game.maximum for n in numbers):
        raise ValueError("invalid pool")
    balls = 20 if game.joker and joker is None else 1
    capacity = comb(len(numbers), game.pick) * balls if len(numbers) >= game.pick else 0
    if budget > capacity:
        raise ValueError("budget exceeds distinct variants in the selected pool")
    rng, chosen = np.random.default_rng(seed), set()
    # Enumeration only near saturation of a SMALL space; no C(49,6) list.
    if capacity <= 100_000 and budget > capacity // 2:
        candidates = [c + ((j,) if game.joker else ())
                      for c in combinations(sorted(numbers), game.pick)
                      for j in (range(1, 21) if game.joker and joker is None else [joker])]
        selected = rng.choice(len(candidates), budget, replace=False)
        return [list(candidates[int(i)]) for i in selected]
    while len(chosen) < budget:
        main = tuple(sorted(map(int, rng.choice(numbers, game.pick, replace=False))))
        ball = int(rng.integers(1, 21)) if game.joker and joker is None else joker
        chosen.add(main + ((int(ball),) if game.joker else ()))
    return [list(line) for line in sorted(chosen)]


def equal_budget_pool(game, pool, scores, budget, guarantee, condition=None, *, seed=0, method=None):
    """Existing production wheel, then explicit top-up to the declared budget.

    A covering can finish below its cap. The experiment adds distinct variants
    from the SAME pool; it never silently changes the comparison's cost.
    """
    from wheeling_methods import generate_wheel, resolve_wheel_method

    condition = None if condition == 0 else condition
    if budget > comb(len(pool), game.pick):
        raise ValueError("pool cannot supply the declared budget")
    method = "lotto" if condition and condition != guarantee else (method or resolve_wheel_method(budget))
    lines, coverage = generate_wheel(
        method, pool, game.pick, guarantee, budget, scores,
        condition, game.drawn, game.maximum,
    )
    unique = {tuple(sorted(map(int, line))): list(map(int, line)) for line in lines}
    if len(unique) > budget:
        raise ValueError("generator exceeded the declared budget")
    original = len(unique)
    if original < budget:
        # A second distinct B-set supplies enough missing variants even when
        # it overlaps the wheel; at most B candidate visits are needed.
        for line in random_tickets(Game(game.key, game.maximum, game.drawn, game.pick), budget, seed, pool=pool):
            unique.setdefault(tuple(line), line)
            if len(unique) == budget:
                break
    return list(unique.values()), {"wheel_variants": original, "top_up": budget - original,
                                   "coverage_before_top_up_pct": coverage, "method": method}


def uniform_draws(game: Game, samples: int, seed: int):
    samples = integer(samples, minimum=1)
    rng = np.random.default_rng(seed)
    # Random keys give uniform subsets; main order is irrelevant to the two
    # hit objectives (5/40 category I is resolved separately for finances).
    keys = rng.random((samples, game.maximum))
    numbers = np.argpartition(keys, game.drawn - 1, axis=1)[:, :game.drawn]
    matrix = np.zeros((samples, game.maximum), dtype=np.int8)
    matrix[np.arange(samples)[:, None], numbers] = 1
    balls = rng.integers(1, 21, samples) if game.joker else None
    return matrix, balls


def winning_matrix(game, tickets, draws, balls=None, *, objective="any_prize"):
    ticket_matrix = np.zeros((len(tickets), game.maximum), dtype=np.int8)
    for i, line in enumerate(tickets):
        ticket_matrix[i, np.asarray(line[:game.pick], dtype=int) - 1] = 1
    hits = draws @ ticket_matrix.T
    if objective == "4plus":
        return hits >= 4
    if objective != "any_prize":
        raise ValueError("objective must be any_prize or 4plus")
    if game.joker:
        matches = balls[:, None] == np.asarray(tickets)[:, -1][None, :]
        return (hits >= 3) | ((hits >= 1) & matches)
    return hits >= (4 if game.key == "loto_5_40" else 3)


def proportion(successes: int, total: int):
    if not total:
        return {"successes": 0, "samples": 0, "probability": None, "ci95": None}
    p, z = successes / total, 1.959963984540054
    denominator = 1 + z * z / total
    middle = (p + z * z / (2 * total)) / denominator
    radius = z * sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return {"successes": int(successes), "samples": int(total), "probability": p,
            "ci95": [max(0., middle - radius), min(1., middle + radius)]}


def compare_probabilities(game: Game, portfolios: dict, *, samples=20_000, seed=271828):
    """Shared independent evaluation draws; Wilson intervals are Monte Carlo error.

    They are NOT confidence bounds on an empirical predictive advantage.
    Memory is bounded by blocks, even for thousands of tickets.
    """
    samples = integer(samples, minimum=1)
    result = {}
    for name, raw in portfolios.items():
        tickets = validate_tickets(game, raw)
        counts = {objective: 0 for objective in ("any_prize", "4plus")}
        # Each portfolio gets exactly the same sample sequence.
        for start in range(0, samples, 512):
            draws, balls = uniform_draws(game, min(512, samples - start), seed + start)
            for objective in counts:
                counts[objective] += int(winning_matrix(game, tickets, draws, balls, objective=objective).any(axis=1).sum())
        result[name] = {
            **{k: proportion(v, samples) for k, v in counts.items()},
            "jackpot_probability_exact": len(tickets) / (comb(game.maximum, game.pick) * (20 if game.joker else 1)),
            "variants": len(tickets), "model": "uniform_independent_draws_current_prize_rules",
            "sample_seed": seed, "cost_recovery_probability": None,
            "cost_recovery_reason": "Future category payouts and shared prize funds are unknown",
        }
    return result


def optimize_universe(game: Game, budget: int, *, objective="4plus", seed=104729,
                      design_samples=4000, proposals=128):
    """Bounded coordinate search over the entire universe, without history.

    Strict improvement of the explicit objective on an independent design
    sample; no claim of a global optimum or improved out-of-sample coverage.
    """
    tickets = random_tickets(game, budget, seed)
    design_samples = integer(design_samples, minimum=1)
    proposals = integer(proposals)
    draws, balls = uniform_draws(game, design_samples, seed + 1)
    wins = winning_matrix(game, tickets, draws, balls, objective=objective)
    counts = wins.sum(axis=1).astype(np.int32)
    initial = int((counts > 0).sum())
    rng, accepted = np.random.default_rng(seed + 2), 0
    known = {tuple(t) for t in tickets}
    for _ in range(proposals):
        slot = int(rng.integers(budget))
        candidate = random_tickets(game, 1, int(rng.integers(2**32)))[0]
        if tuple(candidate) in known:
            continue
        proposed = winning_matrix(game, [candidate], draws, balls, objective=objective)[:, 0]
        after = counts - wins[:, slot] + proposed
        if int((after > 0).sum()) > int((counts > 0).sum()):
            known.remove(tuple(tickets[slot]))
            known.add(tuple(candidate))
            tickets[slot], wins[:, slot], counts = candidate, proposed, after
            accepted += 1
    return tickets, {"objective": objective, "algorithm": "sample_coordinate_search_v1",
                     "seed": seed, "design_samples": design_samples, "proposals": proposals,
                     "accepted": accepted, "design_covered_before": initial,
                     "design_covered_after": int((counts > 0).sum()), "global_optimum": False}


def realized(game: Game, tickets, numbers, day, *, joker=None, official=None,
             variant_price_bani: int, ticket_fees_bani: int | None):
    tickets = validate_tickets(game, tickets)
    price = integer(variant_price_bani, minimum=1)
    fees = None if ticket_fees_bani is None else integer(ticket_fees_bani)
    matches = [resolve_variant_category(game.key, line, numbers, official_joker=joker,
                                       draw_date=day) for line in tickets]
    if any(m.status not in {"matched", "no_prize"} for m in matches):
        raise ValueError("invalid or incomplete draw/variant context")
    variants_cost = len(tickets) * price
    full_cost = None if fees is None else variants_cost + fees
    gross, status, categories = None, "official_prizes_missing", {}
    if official is not None:
        if game.joker and official.joker != joker:
            raise ValueError("official Joker mismatch")
        estimate = estimate_generated_prize(game.key, tickets, official,
                                           target_numbers=numbers, target_date=day,
                                           expected_count=len(tickets))
        status, categories = estimate.status, estimate.category_counts
        gross = estimate.total_bani if status == "ready" else None
    elif not any(m.category for m in matches):
        # No winning variant => zero gross is known even without prize amounts.
        gross, status = 0, "no_winning_variant"
    return {"variants": len(tickets), "any_prize": any(m.category for m in matches),
            "4plus": any(m.main_hits is not None and m.main_hits >= 4 for m in matches),
            "best_main_hits": max(m.main_hits or 0 for m in matches),
            "variant_cost_bani": variants_cost, "ticket_fees_bani": fees,
            "full_cost_bani": full_cost, "gross_bani": gross,
            "net_bani": gross - full_cost if gross is not None and full_cost is not None else None,
            "recovered_cost": gross >= full_cost if gross is not None and full_cost is not None else None,
            "prize_status": status, "category_counts": categories,
            "financial_basis": "gross_estimate_at_published_unit_prizes_before_tax; not proof of purchase"}
