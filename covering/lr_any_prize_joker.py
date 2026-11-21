"""Exact chance of any Romanian Joker prize for disjoint official scheme pools."""

from functools import lru_cache
from math import comb
from operator import index

from covering import lr_schemes as catalog
from covering.probability import wheel_hit_profile
from loto_enterprise.core.lotteries import require_lottery


@lru_cache(maxsize=32)
def _losing_subsets(scheme, drawn):
    """Count inside-pool subsets of each size that miss 3+ on every row."""
    profile = wheel_hit_profile(range(1, scheme.n + 1), scheme.variants)
    return tuple(comb(scheme.n, k) - profile[k][3] for k in range(drawn + 1))


def _convolve(left, right):
    return tuple(sum(left[i] * right[k - i] for i in range(k + 1))
                 for k in range(len(left)))


def recommend_joker_any_prize(budget_bani: int) -> dict | None:
    """Best disjoint portfolio under the full ticket budget, one distinct Joker per zone.

    This is globally optimal over complete LR zones when every affordable multiset
    fits on disjoint pools. Fix a drawn Joker and move one zone's shared main ball
    x to a fresh y. Draws containing both or neither have the same outcome. Pair
    draws containing just x with those containing just y. Prize events increase
    with main hits: splitting the two zones' uses of x preserves or increases the
    number of winners in each pair. Repeat until disjoint, then relabel the balls
    in official positional order. Distinct Jokers also maximize the number of
    winning bonus outcomes for every main draw. If a multiset cannot be split,
    only the disjoint-family maximum is reported. An unaffordable game returns None.
    """
    from covering.lr_portfolio import cost_bani

    try:
        budget = index(budget_bani)
    except TypeError as exc:
        raise ValueError('Bugetul trebuie exprimat în bani întregi.') from exc
    if isinstance(budget_bani, bool) or budget < 0:
        raise ValueError('Bugetul trebuie să fie un număr întreg de bani, cel puțin zero.')
    lot = require_lottery('joker', country='RO')
    schemes = sorted(catalog.lr_schemes('joker'), key=lambda scheme: scheme.code)
    manifest = {(code, n) for game, code, n in catalog._manifest(str(catalog.LR_DIR))
                if game == 'joker'}
    if not manifest or manifest != {(scheme.code, scheme.n) for scheme in schemes}:
        raise ValueError('Catalogul de scheme LR este incomplet pentru Joker.')

    drawn, maximum = lot.draw_n, lot.max_n
    joker_count = lot.geo.second.max_n
    unit = round(lot.price * 100)
    denominator = joker_count * comb(maximum, drawn)
    best = None
    best_order = None
    all_fit = True

    def visit(start, pieces, variants, used, no_three, matched_joker):
        nonlocal best, best_order, all_fit
        for i in range(start, len(schemes)):
            scheme = schemes[i]
            zones = len(pieces) + 1
            size = variants + scheme.size
            price = cost_bani(size, zones, unit, lot.per_ticket)
            if price > budget:
                continue
            occupied = used + scheme.n
            if occupied > maximum or zones > joker_count:
                all_fit = False
                continue

            # For a losing draw every unmatched zone must miss 3+. A zone with
            # the drawn Joker must miss all its main balls (every position is played).
            profile = _losing_subsets(scheme, drawn)
            all_miss = _convolve(no_three, profile)
            previous_matches = _convolve(matched_joker, profile)
            one_matches = tuple(a + b for a, b in zip(previous_matches, no_three))
            losses = sum(((joker_count - zones) * all_miss[k] + one_matches[k])
                         * comb(maximum - occupied, drawn - k) for k in range(drawn + 1))
            favorable = denominator - losses
            selected = (*pieces, scheme)
            order = (-favorable, price, zones, tuple(s.code for s in selected))
            if best_order is None or order < best_order:
                best_order = order
                best = selected, size, price, favorable
            visit(i, selected, size, occupied, all_miss, one_matches)

    visit(0, (), 0, 0, (1,) + (0,) * drawn, (0,) * (drawn + 1))
    if best is None:
        return None
    selected, size, price, favorable = best
    pools = []
    first = 1
    for scheme in selected:
        pools.append(list(range(first, first + scheme.n)))
        first += scheme.n
    return dict(codes=[scheme.code for scheme in selected], example_pools=pools,
                price_bani=price, variants=size, attained=favorable, denominator=denominator,
                optimal=all_fit, upper=favorable if all_fit else None, anchor_size=0,
                joker_numbers=list(range(1, len(selected) + 1)))
