"""Exact 6/49 prize coverage for portfolios with disjoint official LR pools."""

from covering.lr_catalog_cache import catalog_cache
from dataclasses import dataclass
from itertools import combinations, combinations_with_replacement
from math import comb
from operator import index

from covering import lr_schemes as catalog
from covering.lr_portfolio import _unrestricted_bounds, cost_bani
from loto_enterprise.core.lotteries import require_lottery


@dataclass(frozen=True)
class _Code:
    code: int
    n: int
    size: int
    favorable: int
    triples: int


@catalog_cache(maxsize=1)
def _catalogue():
    lot = require_lottery("6/49", country="RO")
    schemes = catalog.lr_schemes("6/49")
    expected = {(c, n) for game, c, n in catalog._manifest(str(catalog.LR_DIR)) if game == "6/49"}
    if not expected or expected != {(s.code, s.n) for s in schemes}:
        raise ValueError("Catalogul de scheme LR este incomplet pentru 6/49.")

    options = []
    for scheme in sorted(schemes, key=lambda s: s.code):
        triples = {triple for row in scheme.variants for triple in combinations(row, 3)}
        favorable = catalog.scheme_numerators(scheme)[3]
        options.append(_Code(scheme.code, scheme.n, scheme.size, favorable, len(triples)))

    denominator = comb(lot.max_n, lot.draw_n)
    unit = round(lot.price * 100)
    # Once a repeated code's *sum* covers the denominator, the union upper
    # bound is already 100%. Larger requested budgets need no larger table.
    saturation_costs = []
    for item in options:
        copies = (denominator + item.favorable - 1) // item.favorable
        saturation_costs.append(cost_bani(copies * item.size, copies, unit, lot.per_ticket))
    bounds = _unrestricted_bounds(options, min(saturation_costs) // unit, unit, lot.per_ticket)
    return options, denominator, unit, lot.per_ticket, lot.max_n, bounds


def recommend_649(budget_bani: int) -> dict:
    """Maximize the number of winning draws among affordable disjoint LR pools.

    If all affordable multisets fit disjointly, splitting shared labels proves
    global optimality. Replace one zone's use of a shared ball x by a fresh y.
    Pair the draws containing just x with the draws containing just y. On that
    pair each zone's prize event is increasing in its own hit, so separating
    the uses can only increase their union. Draws containing both or neither
    retain their outcomes. Once disjoint, each zone can be relabelled in the
    official positional order without changing its odds. Otherwise, the sum
    of each zone's winning draws remains a valid upper bound for all layouts.
    """
    try:
        budget = index(budget_bani)
    except TypeError as exc:
        raise ValueError("Bugetul pentru 6/49 trebuie exprimat în bani întregi.") from exc
    if budget < 10_000:
        raise ValueError("Bugetul minim pentru 6/49 este de 100 lei.")

    options, denominator, unit, zones, universe, bounds = _catalogue()
    best_key = None
    best = ()
    all_fit = True
    # A longer affordable list that will not fit contains a non-fitting prefix
    # of at most max_zones + 1. No larger multiset needs to be enumerated.
    max_zones = universe // min(item.n for item in options)
    for count in range(1, max_zones + 2):
        for group in combinations_with_replacement(options, count):
            variants = sum(item.size for item in group)
            price = cost_bani(variants, count, unit, zones)
            if price > budget:
                continue
            if sum(item.n for item in group) > universe:
                all_fit = False
                continue
            # Two disjoint pools can both win only on an exact split of
            # three drawn balls each; three pools cannot win on six balls.
            joint = sum(a.triples * b.triples for a, b in combinations(group, 2))
            favorable = sum(item.favorable for item in group) - joint
            key = (-favorable, price, count, tuple(item.code for item in group))
            if best_key is None or key < best_key:
                best_key, best = key, group

    if best_key is None:
        raise ValueError("Nicio schemă oficială 6/49 nu se încadrează în buget.")
    attained, price = -best_key[0], best_key[1]
    upper = attained if all_fit else min(
        denominator, max(total for cost, total in bounds.items() if cost <= budget)
    )
    pools = []
    offset = 0
    for item in best:
        pools.append(list(range(offset + 1, offset + item.n + 1)))
        offset += item.n
    return dict(
        codes=[item.code for item in best], example_pools=pools, price_bani=price,
        variants=sum(item.size for item in best), attained=attained, denominator=denominator,
        upper=upper, optimal=attained == upper, anchor_size=0,
    )
