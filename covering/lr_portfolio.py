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


@lru_cache(maxsize=3)
def _study(game):
    lot = require_lottery(game, country="RO")
    unit = round(lot.price * 100)
    schemes = sorted(catalog.lr_schemes(game), key=lambda s: s.code)
    manifest = {(code, n) for g, code, n in catalog._manifest(str(catalog.LR_DIR)) if g == game}
    if not manifest or manifest != {(s.code, s.n) for s in schemes}:
        raise ValueError(f"Catalogul de scheme LR este incomplet pentru {game}.")
    exact = {s.code: catalog.scheme_numerators(s) for s in schemes}
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
            price, (best, codes) = min(((p, entry) for p, entry in lower.items() if p <= budget),
                                       key=lambda pair: (-pair[1][0], pair[0], len(pair[1][1]), pair[1][1]))
            assert exact[original.code][t] <= best <= bound
            pieces = [by_code[c] for c in codes]
            required = sum(s.n for s in pieces)
            overlaps = max(0, required - lot.max_n)
            shared = (overlaps + len(pieces) - 2) // (len(pieces) - 1) if len(pieces) > 1 else 0
            assert shared <= 2 * t - lot.draw_n - 1
            offset = shared
            pools = []
            for s in pieces:
                pool = list(range(1, shared + 1)) + list(range(offset + 1, offset + s.n - shared + 1))
                offset += s.n - shared
                pools.append(pool)
            assert offset <= lot.max_n
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
