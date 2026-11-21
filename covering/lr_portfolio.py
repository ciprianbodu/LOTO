"""Certified main-urn hit probabilities for portfolios of complete Romanian LR codes.

An official code occupies one physical ticket zone. The maximum probability is
certified only when a feasible construction attains the union bound over all
affordable multisets of codes, including repetitions and arbitrary overlaps.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from itertools import combinations
from math import comb
from numbers import Integral

from covering import lr_schemes as catalog
from covering.lr_selection import resolve_lr_scheme
from loto_enterprise.core.lotteries import require_lottery

# Standard physical ticket fee, in bani: https://www.loto.ro/?page_id=1068
FEE_BANI = 50


@dataclass(frozen=True)
class _Option:
    code: int
    n: int
    size: int
    favorable: int


def cost_bani(variants, marks, unit_bani, zones):
    return variants * unit_bani + FEE_BANI * ((marks + zones - 1) // zones)


def _unrestricted_bounds(options, max_variants, unit, zones):
    """Union bound. Keep the best sum for every exact variant and marked-zone count."""
    by_marks = {0: 0}
    best_by_price = {}
    for marks in range(1, max_variants // min(o.size for o in options) + 1):
        following = {}
        for variants, total in by_marks.items():
            for item in options:
                size = variants + item.size
                if size <= max_variants:
                    following[size] = max(following.get(size, 0), total + item.favorable)
        for size, total in following.items():
            price = cost_bani(size, marks, unit, zones)
            best_by_price[price] = max(best_by_price.get(price, 0), total)
        by_marks = following
    return best_by_price


def _constructible(options, maximum, drawn, threshold, unit, zones, budget):
    """Enumerate multisets whose target events can be made mutually exclusive.

    All pools share an anchor of at most 2*threshold-drawn-1 numbers. Two
    threshold hits would then need at least drawn+1 distinct drawn numbers.
    Each pool uses fresh numbers outside that anchor. Fully disjoint pools
    are sufficient where the total number of positions fits.
    """
    shared = 2 * threshold - drawn - 1
    found = {}

    def visit(start, path, size, private, favorable):
        for i in range(start, len(options)):
            item = options[i]
            new_size = size + item.size
            price = cost_bani(new_size, len(path) + 1, unit, zones)
            new_private = private + item.n - shared
            if price > budget or shared + new_private > maximum:
                continue
            choices = (*path, item.code)
            total = favorable + item.favorable
            # At a fixed price prefer fewer zones, then smaller codes, for ties.
            if (price not in found or (-total, len(choices), choices) <
                    (-found[price][0], len(found[price][1]), found[price][1])):
                found[price] = (total, choices)
            visit(i, choices, new_size, new_private, total)

    visit(0, (), 0, 0, 0)
    return found


def _best_at_budget(constructions, budget):
    return min(
        (entry for entry in constructions.items() if entry[0] <= budget),
        key=lambda row: (-row[1][0], row[0], len(row[1][1]), row[1][1]),
        default=(0, (0, ())),
    )


def _catalog_data(game):
    lot = require_lottery(game, country="RO")
    schemes = sorted(catalog.lr_schemes(game), key=lambda s: s.code)
    manifest = {(code, n) for g, code, n in catalog._manifest(str(catalog.LR_DIR)) if g == game}
    if not manifest or manifest != {(s.code, s.n) for s in schemes}:
        raise ValueError(f"Catalogul de scheme LR este incomplet pentru {game}.")
    exact = {s.code: catalog.scheme_numerators(s) for s in schemes}
    return lot, schemes, exact


def _example_pools(pieces, maximum):
    # Use the smallest common anchor that leaves enough distinct private numbers.
    count = len(pieces)
    excess = max(0, sum(piece.n for piece in pieces) - maximum)
    shared = (excess + count - 2) // (count - 1) if count > 1 else 0
    offset = shared
    pools = []
    for piece in pieces:
        end = offset + piece.n - shared
        pools.append([*range(1, shared + 1), *range(offset + 1, end + 1)])
        offset = end
    assert offset <= maximum
    return shared, pools


@lru_cache(maxsize=3)
def _study(game):
    lot, schemes, exact = _catalog_data(game)
    unit = round(lot.price * 100)
    # If six numbers are drawn, two disjoint pools can both hit 3+ only
    # through one covered triple in each. A third such hit is impossible.
    triple_counts = {
        s.code: len({triple for ticket in s.variants for triple in combinations(ticket, 3)})
        for s in schemes
    } if lot.draw_n == 6 else {}
    by_code = {s.code: s for s in schemes}
    cap = max(s.size for s in schemes)
    greatest_budget = cost_bani(cap, 1, unit, lot.per_ticket)
    denominator = comb(lot.max_n, lot.draw_n)
    rows = []
    for t in (4, 5):
        options = [_Option(s.code, s.n, s.size, exact[s.code][t]) for s in schemes]
        upper = _unrestricted_bounds(options, cap, unit, lot.per_ticket)
        lower = _constructible(options, lot.max_n, lot.draw_n, t, unit, lot.per_ticket, greatest_budget)
        for original in schemes:
            budget = cost_bani(original.size, 1, unit, lot.per_ticket)
            bound = min(denominator, max(total for price, total in upper.items() if price <= budget))
            price, (best, codes) = _best_at_budget(lower, budget)
            assert exact[original.code][t] <= best <= bound
            pieces = [by_code[c] for c in codes]
            shared, pools = _example_pools(pieces, lot.max_n)
            assert shared <= 2 * t - lot.draw_n - 1
            side_counts = {}
            for threshold in range(3, lot.pick_n + 1):
                if len(pieces) == 1 or shared < 2 * threshold - lot.draw_n:
                    side_counts[threshold] = sum(exact[s.code][threshold] for s in pieces)
            if shared == 0 and lot.draw_n == 6 and len(pieces) > 1:
                both = sum(triple_counts[a.code] * triple_counts[b.code]
                           for a, b in combinations(pieces, 2))
                side_counts[3] = sum(exact[s.code][3] for s in pieces) - both
            assert side_counts[t] == best
            rows.append(dict(game=game, base=original.code, threshold=t, budget_bani=budget,
                             price_bani=price, base_hits=exact[original.code][t], denominator=denominator,
                             attained=best, upper=bound, optimal=best == bound, variants=sum(s.size for s in pieces),
                             codes=list(codes), anchor_size=shared, example_pools=pools,
                             certified_other_thresholds=side_counts, base_thresholds=exact[original.code]))
    return rows


def compare_portfolios(game, base_code):
    """Isolated copies of the 4+ and 5+ comparisons under this code's full ticket price."""
    base = resolve_lr_scheme(game, base_code)
    return [deepcopy(r) for r in _study(game) if r["base"] == base.code]


@lru_cache(maxsize=128)
def _budget_record(game, budget, threshold):
    lot, schemes, exact = _catalog_data(game)
    if not 4 <= threshold <= lot.pick_n:
        raise ValueError(f"Pragul pentru {game} trebuie să fie între 4 și {lot.pick_n}.")
    unit, zones = round(lot.price * 100), lot.per_ticket
    options = [
        _Option(s.code, s.n, s.size, exact[s.code][threshold]) for s in schemes
    ]
    denominator = comb(lot.max_n, lot.draw_n)
    # The anchor construction has at most this much private space. Every code
    # consumes n-anchor positions, so its variants/position ratio also bounds
    # the total affordable size at which these constructions stop changing.
    anchor = 2 * threshold - lot.draw_n - 1
    space = lot.max_n - anchor
    max_marks = space // min(item.n - anchor for item in options)
    max_variants = max(space * item.size // (item.n - anchor) for item in options)
    construction_cap = cost_bani(max_variants, max_marks, unit, zones)
    lower = _constructible(
        options, lot.max_n, lot.draw_n, threshold, unit, zones, min(budget, construction_cap)
    )
    price, (best, codes) = _best_at_budget(lower, budget)
    if not codes:
        upper = 0
    elif budget <= construction_cap:
        affordable = (budget - FEE_BANI) // unit
        bounds = _unrestricted_bounds(options, affordable, unit, zones)
        upper = min(denominator, max(total for p, total in bounds.items() if p <= budget))
    else:
        # Arbitrarily many codes of any overlap remain legal. Each mark costs
        # at least size*unit + fee/zones, since physical tickets round up.
        # Apply the largest favorable-draws/price ratio to the entire budget.
        upper = min(denominator, max(
            budget * item.favorable * zones // (item.size * unit * zones + FEE_BANI)
            for item in options
        ))
    by_code = {s.code: s for s in schemes}
    pieces = [by_code[code] for code in codes]
    shared, pools = _example_pools(pieces, lot.max_n)
    assert best <= upper and shared <= anchor
    return dict(
        game=game, threshold=threshold, budget_bani=budget, price_bani=price,
        attained=best, denominator=denominator, upper=upper, optimal=best == upper,
        variants=sum(s.size for s in pieces), codes=list(codes), anchor_size=shared,
        example_pools=pools,
    )


def recommend_main_hits(game: str, budget_bani: int, threshold: int = 4) -> dict:
    """Recommend complete Romanian LR codes at a physical-ticket budget in bani.

    attained / denominator is the exact chance of reaching the threshold on at
    least one variant. upper bounds every affordable arrangement, including
    repeated codes and overlapping pools. An empty plan costs zero.
    """
    if (
        isinstance(budget_bani, bool)
        or not isinstance(budget_bani, Integral)
        or budget_bani < 0
    ):
        raise ValueError("Bugetul trebuie să fie un număr întreg de bani, cel puțin zero.")
    if isinstance(threshold, bool) or not isinstance(threshold, Integral):
        raise ValueError("Pragul trebuie să fie un număr întreg.")
    return deepcopy(_budget_record(game, int(budget_bani), int(threshold)))
