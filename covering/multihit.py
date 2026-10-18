"""Experimental covering designs with multiple distinct tickets per target.

An uncapped wheel covers each ``guarantee``-subset at least ``min_covers``
times, when that is possible with distinct tickets. A capped wheel is best
effort: its returned percentage describes primary coverage only. Multiplicity
statistics describe target subsets, not an unconditional draw win rate.
Increasing multiplicity may reduce primary hit probability at the same cost;
this objective is therefore explicit opt-in, never the automatic choice.
"""

from __future__ import annotations

import itertools
import math
from collections import Counter

from covering.common import _coverage_pct, _coverage_ratio_pct, _sorted_pool, ensure_pool_numbers_on_tickets
from covering.probability import _integer


def multi_hit_stats(wheel: list[list[int]], pool: list[int], guarantee: int) -> dict[str, float | int]:
    """Count distinct tickets containing each guarantee-subset of the pool.

    Duplicate ticket rows count once. Invalid ticket numbers are rejected so
    repeated numbers cannot inflate the multiplicity certificate. A complete
    double cover means two distinct tickets contain every target; under a
    budget, neither this nor primary coverage is necessarily complete.
    """
    nums = tuple(_integer(n) for n in pool)
    g = _integer(guarantee)
    if g < 1 or len(set(nums)) != len(nums):
        raise ValueError("Pool numbers must be distinct and guarantee positive")
    targets = list(itertools.combinations(sorted(nums), g))
    pool_set = set(nums)
    tickets = set()
    for row in wheel:
        ticket = tuple(sorted(_integer(n) for n in row))
        if not ticket or len(set(ticket)) != len(ticket) or not set(ticket) <= pool_set:
            raise ValueError("Ticket must contain distinct pool numbers")
        tickets.add(ticket)
    target_counts = Counter(sub for ticket in tickets for sub in itertools.combinations(ticket, g))
    counts = [target_counts[t] for t in targets]
    out = {"total_targets": len(targets)}
    for level in (1, 2, 3):
        covered = sum(c >= level for c in counts)
        out[f"covered_{level}plus"] = covered
        out[f"pct_{level}plus"] = _coverage_ratio_pct(covered, len(targets))
    out["min_covers"] = min(counts, default=0)
    out["max_covers"] = max(counts, default=0)
    return out


def wheel_multihit(pool, pick=6, guarantee=4, max_variants=0, scores=None, min_covers=2) -> tuple[list[list[int]], float]:
    """Construct a deterministic multiple cover, or a capped best effort.

    Candidate tickets contain a least-covered pivot target. Within that
    search, primary coverage has strict priority over the second layer, then
    the third, etc. This is a greedy heuristic, not a proof of optimality.
    An impossible uncapped multiplicity raises ValueError rather than
    silently claiming a guarantee. Repeating a ticket is never permitted.
    """
    pick, guarantee, budget, multiplicity = map(_integer, (pick, guarantee, max_variants, min_covers))
    if not 1 <= guarantee <= pick or multiplicity < 1:
        raise ValueError("Require 1 <= guarantee <= pick and min_covers >= 1")
    pool = _sorted_pool([_integer(n) for n in pool], scores)
    if len(set(pool)) != len(pool):
        raise ValueError("Pool numbers must be distinct")
    v = len(pool)
    if v < pick:
        return [], 0.0
    possible_covers = math.comb(v - guarantee, pick - guarantee)
    if budget <= 0 and multiplicity > possible_covers:
        raise ValueError(f"min_covers={multiplicity} is impossible with distinct tickets; maximum is {possible_covers}")

    if guarantee == pick:
        combinations = itertools.combinations(pool, pick)
        if budget > 0:
            combinations = itertools.islice(combinations, budget)
        wheel = [sorted(c) for c in combinations]
        if budget > 0:
            wheel = ensure_pool_numbers_on_tickets(wheel, pool, pick)
        return wheel, _coverage_pct(wheel, pool, guarantee)

    targets = [tuple(sorted(t)) for t in itertools.combinations(pool, guarantee)]
    target_index = {target: i for i, target in enumerate(targets)}
    counts = [0] * len(targets)
    wheel = []
    selected = set()
    target_cache = {}

    def ticket_targets(ticket):
        if ticket not in target_cache:
            target_cache[ticket] = tuple(target_index[sub] for sub in itertools.combinations(ticket, guarantee))
        return target_cache[ticket]

    # Every iteration adds one distinct ticket. There is no arbitrary early
    # cutoff that could leave an uncapped, feasible guarantee unfulfilled.
    limit = math.comb(v, pick)
    if budget > 0:
        limit = min(limit, budget)
    layers = min(multiplicity, possible_covers)
    while len(wheel) < limit:
        minimum = min(counts)
        if minimum >= layers:
            break
        pivot = targets[counts.index(minimum)]
        remaining = [n for n in pool if n not in pivot]
        best_ticket, best_gain = None, None
        for extras in itertools.combinations(remaining, pick - guarantee):
            ticket = tuple(sorted((*pivot, *extras)))
            if ticket in selected:
                continue
            histogram = [0] * layers
            for idx in ticket_targets(ticket):
                if counts[idx] < layers:
                    histogram[counts[idx]] += 1
            gain = tuple(histogram)
            if best_gain is None or gain > best_gain:
                best_ticket, best_gain = ticket, gain
        if best_ticket is None:
            # A deficient target always has an unused covering ticket when
            # layers <= possible_covers. This guard should be unreachable.
            raise RuntimeError("Could not fulfill a feasible multiple cover")
        wheel.append(list(best_ticket))
        selected.add(best_ticket)
        for idx in ticket_targets(best_ticket):
            counts[idx] += 1

    if budget > 0:
        wheel = ensure_pool_numbers_on_tickets(wheel, pool, pick)
    # Pool repair can replace numbers. Certify the delivered tickets, not the
    # counters of the earlier construction.
    return wheel, _coverage_pct(wheel, pool, guarantee)
