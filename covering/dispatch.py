"""Wheel method dispatcher."""

from __future__ import annotations

import os

from budget_cover import wheel_hitcover, wheel_maxcover
from covering.common import (
    _coverage_pct,
    _greedy_fallback,
    _sorted_pool,
    lotto_coverage_pct,
    ensure_pool_numbers_on_tickets,
)
from covering.designs import wheel_lajolla, wheel_lotto, wheel_union34
from covering.higher_hits import improve_higher_hits
from covering.ilp import wheel_ilp
from covering.profile_swap import improve_hit_profile
from covering.search import wheel_annealing, wheel_genetic

WHEEL_METHODS = {
    "hitcover": wheel_hitcover,
    "maxcover": wheel_maxcover,
    "ilp": wheel_ilp,
    "annealing": wheel_annealing,
    "genetic": wheel_genetic,
    "lajolla": wheel_lajolla,
    "union34": wheel_union34,
}


def resolve_wheel_method(max_variants=0, requested: str | None = None) -> str:
    """Production choice shared by engine, physical tickets and WF cache.

    Fixed budgets automatically try exact hit dominance; uncapped generation
    uses the validated covering designs. Explicit overrides remain available.
    """
    if requested is None:
        requested = os.environ.get("LOTO_WHEEL_METHOD", "")
    requested = requested.strip().lower()
    if requested:
        if requested in WHEEL_METHODS or requested == "greedy":
            return requested
        return "greedy"
    return "hitcover" if int(max_variants or 0) > 0 else "lajolla"


def generate_wheel(
    method: str,
    pool,
    pick,
    guarantee,
    max_variants=0,
    scores=None,
    condition: int | None = None,
    draw_n: int | None = None,
):
    """Selectează algoritmul de wheeling. 'greedy' (sau necunoscut) → canonic.

    `condition` (numărul de numere din pool care trebuie să cadă ca garanția să
    se aplice) > `guarantee` comută pe lotto design „t dacă p" (`wheel_lotto`),
    indiferent de `method`: designurile locale și coverele clasice există doar
    pentru p == t. `condition` None sau egal cu garanția = cover clasic.
    Un cover clasic 4 complet, fără plafon, este rafinat pentru hituri 5+ când
    se extrag 6 numere. `draw_n` omis păstrează geometria `pick`; 5/40 transmite
    explicit 6. Metoda greedy explicită păstrează construcția de referință.
    """
    if int(guarantee) > int(pick):
        # Pipeline-ul clampeaza deja; API-ul direct arunca altfel
        # `ValueError: r must be non-negative` din itertools, fara context.
        raise ValueError(
            f"guarantee={guarantee} > pick={pick}: garanția nu poate depăși numerele extrase"
        )
    if condition is not None and int(condition) != int(guarantee):
        if int(condition) < int(guarantee):
            raise ValueError(f"condition={condition} < guarantee={guarantee}")
        wheel, cov = wheel_lotto(
            pool, pick, guarantee, int(condition), max_variants, scores
        )
        improved, audit = improve_hit_profile(
            _sorted_pool(pool, scores), wheel, draw_n=draw_n
        )
        if audit["applied"]:
            wheel = improved
            cov = lotto_coverage_pct(wheel, pool, guarantee, int(condition))
        return wheel, cov
    fn = WHEEL_METHODS.get((method or "greedy").strip().lower())
    if fn is None:
        wheel, cov = _greedy_fallback(pool, pick, guarantee, max_variants, scores)
    else:
        wheel, cov = fn(pool, pick, guarantee, max_variants, scores)
    if int(max_variants or 0) > 0:
        wheel = ensure_pool_numbers_on_tickets(wheel, pool, pick)
        cov = _coverage_pct(wheel, pool, guarantee)
    elif (
        fn is not None
        and int(max_variants or 0) == 0
        and int(guarantee) == 4
        and int(pick) in (5, 6)
        and (int(pick) if draw_n is None else draw_n) == 6
        and cov >= 100.0
    ):
        wheel, _higher_audit = improve_higher_hits(
            _sorted_pool(pool, scores), wheel, draw_n=6
        )
        cov = _coverage_pct(wheel, pool, guarantee)
    if fn is not None:
        # Generic exact-dominance swaps (any guarantee, any budget): same
        # distinct ticket count, no profile entry lower, strict 3+/4+/5+ gain.
        improved, audit = improve_hit_profile(
            _sorted_pool(pool, scores), wheel, draw_n=draw_n
        )
        if audit["applied"]:
            wheel = improved
            cov = _coverage_pct(wheel, pool, guarantee)
    return wheel, cov
