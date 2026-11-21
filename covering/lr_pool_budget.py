"""Affordable official LR zones drawn from one fixed main pool."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from functools import lru_cache
from itertools import combinations
from math import comb
from random import Random

import numpy as np

from covering.lr_budget import ANY_PRIZE
from covering.lr_portfolio import cost_bani
from covering.lr_schemes import lr_schemes
from covering.probability import MAX_EXACT_POOL
from loto_enterprise.core.lotteries import require_lottery


@dataclass(frozen=True, slots=True)
class _Zone:
    code: int
    positions: tuple[int, ...]
    variants: int
    wins: int
    touch: int


def _pack(values):
    return int.from_bytes(np.packbits(values, bitorder="little").tobytes(), "little")


@lru_cache(maxsize=32)
def _draw_space(size, drawn, universe):
    """An integer bitset over the attainable intersections with this pool."""
    masks = np.arange(1 << size, dtype=np.uint32)
    counts = np.bitwise_count(masks)
    valid = (counts <= drawn) & (counts >= drawn - (universe - size))
    masks, counts = masks[valid], counts[valid]
    balls = tuple(_pack(masks & (1 << i) != 0) for i in range(size))
    layers = tuple(
        (_pack(counts == k), comb(universe - size, drawn - k))
        for k in range(max(0, drawn - (universe - size)), min(size, drawn) + 1)
    )
    return balls, layers


def _weighted(events, layers):
    return sum((events & layer).bit_count() * weight for layer, weight in layers)


def _placements(size, width):
    positions = tuple(range(width))
    if comb(width, size) <= 64:
        return combinations(positions, size)
    # Include every cyclic block, then a reproducible sample of other layouts.
    # Only LR positions are sampled. The chosen balls already belong to the pool.
    layouts = {
        tuple(sorted((start + i) % width for i in range(size)))
        for start in positions
    }
    rng = Random(width * 100 + size)
    layouts.update(tuple(sorted(rng.sample(positions, size))) for _ in range(64))
    return sorted(layouts)


@lru_cache(maxsize=32)
def _zone_catalog(game, size, threshold, use_bonus, codes):
    lot = require_lottery(game, "RO")
    balls, _ = _draw_space(size, lot.draw_n, lot.max_n)

    @lru_cache(maxsize=None)
    def covered(indices):
        events = balls[indices[0]]
        for i in indices[1:]:
            events &= balls[i]
        return events

    options = []
    for scheme in lr_schemes(game):
        if scheme.code not in codes:
            continue
        targets = {part for row in scheme.variants for part in combinations(row, threshold)}
        for placement in _placements(scheme.n, size):
            wins = touch = 0
            for part in targets:
                wins |= covered(tuple(placement[p - 1] for p in part))
            if use_bonus:
                for p in placement:
                    touch |= balls[p]
            options.append(_Zone(scheme.code, placement, scheme.size, wins, touch))
    return tuple(options)


def _pool_hits(size, lot, threshold):
    return sum(
        comb(size, k) * comb(lot.max_n - size, lot.draw_n - k)
        for k in range(threshold, min(size, lot.draw_n) + 1)
    )


def _layout_bound(plan, lot, size, threshold, bonus_count):
    bound = _pool_hits(size, lot, threshold)
    if bonus_count:
        bound = lot.geo.second.max_n * bound + bonus_count * (_pool_hits(size, lot, 1) - bound)
    if plan["upper"] is not None:
        bound = min(bound, plan["upper"])
    return bound


def _search(options, lotsize, unit, budget, layers, bonus_slots, urn_size):
    """Grow affordable portfolios from each standalone code and an empty start."""
    best, best_value, best_price = (), 0, 0

    def extend(zone, main, extras, slot):
        wins = main | zone.wins
        value = _weighted(wins, layers)
        if bonus_slots:
            extras = extras[:slot] + (extras[slot] | zone.touch,) + extras[slot + 1:]
            value *= urn_size
            value += sum(_weighted(touch & ~wins, layers) for touch in extras if touch)
        return value, wins, extras

    seeds = {}
    for zone in options:
        seeds.setdefault(zone.code, zone)
    for first in (None, *seeds.values()):
        selected, size, main, value, price = (), 0, 0, 0, 0
        extras = (0,) * bonus_slots
        while True:
            slot = len(selected) % max(1, bonus_slots)
            winner = None
            for zone in options if selected or first is None else (first,):
                cost = cost_bani(size + zone.variants, len(selected) + 1, unit, lotsize)
                if cost > budget:
                    continue
                attained, wins, touches = extend(zone, main, extras, slot)
                gain = attained - value
                if gain > 0:
                    key = (Fraction(gain, cost - price), gain, -cost)
                    if winner is None or key > winner[0]:
                        winner = key, zone, cost, attained, wins, touches
            if winner is None:
                break
            _, zone, price, value, main, extras = winner
            size += zone.variants
            selected += (zone,)
            if (value, -price, -len(selected)) > (best_value, -best_price, -len(best)):
                best, best_value, best_price = selected, value, price
    return best, best_value, best_price


@lru_cache(maxsize=128)
def _layout(game, size, budget_bani, objective, bonus_count):
    lot = require_lottery(game, "RO")
    width = min(size, MAX_EXACT_POOL)
    unit = round(lot.price * 100)
    use_bonus = game == "joker" and objective == ANY_PRIZE
    threshold = 3 if objective == ANY_PRIZE and game != "5/40" else 4
    codes = tuple(
        s.code for s in lr_schemes(game)
        if s.n <= width and cost_bani(s.size, 1, unit, lot.per_ticket) <= budget_bani
    )
    if not codes:
        return (), 0, 0
    _, layers = _draw_space(width, lot.draw_n, lot.max_n)
    options = _zone_catalog(game, width, threshold, use_bonus, codes)
    return _search(
        options, lot.per_ticket, unit, budget_bani, layers,
        bonus_count if use_bonus else 0, lot.geo.second.max_n if use_bonus else 0,
    )


def _attach(plan, lot, zones, pool, jokers, price, attained, upper):
    rows = [
        {
            "code": code,
            "pool_size": len(positions),
            "numbers": [pool[p] for p in positions],
            "joker": jokers[i % len(jokers)] if jokers else None,
        }
        for i, (code, positions, _) in enumerate(zones)
    ]
    plan.pop("anchor_size", None)
    plan.update(
        tickets=[rows[i:i + lot.per_ticket] for i in range(0, len(rows), lot.per_ticket)],
        price_bani=price,
        variants=sum(count for _, _, count in zones),
        attained=attained,
        upper=upper,
        optimal=attained == upper,
    )
    return plan


def restrict_to_pool(plan, objective, pool, jokers=()):
    """Replace the unrestricted layout with whole official zones inside this pool."""
    lot = require_lottery(plan["game"], "RO")
    pool = tuple(sorted(pool))
    template = [zone for ticket in plan["tickets"] for zone in ticket]
    use_bonus = plan["game"] == "joker" and objective == ANY_PRIZE
    threshold = 3 if objective == ANY_PRIZE and plan["game"] != "5/40" else 4
    upper = _layout_bound(plan, lot, len(pool), threshold, len(jokers) if use_bonus else 0)
    template_fits = (
        template and max(n for zone in template for n in zone["numbers"]) <= len(pool)
        and (not use_bonus or len(template) <= len(jokers))
    )
    if not template_fits or plan["attained"] != upper:
        options, attained, price = _layout(plan["game"], len(pool), plan["budget_bani"], objective, len(jokers))
        if not template_fits or (attained, -price) > (plan["attained"], -plan["price_bani"]):
            return _attach(
                plan, lot, [(z.code, z.positions, z.variants) for z in options],
                pool, jokers, price, attained, upper,
            )

    sizes = {scheme.code: scheme.size for scheme in lr_schemes(plan["game"])}
    zones = [(z["code"], tuple(n - 1 for n in z["numbers"]), sizes[z["code"]]) for z in template]
    return _attach(plan, lot, zones, pool, jokers, plan["price_bani"], plan["attained"], upper)
