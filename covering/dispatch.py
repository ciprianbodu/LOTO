"""Wheel method dispatcher."""

from __future__ import annotations

import os
from functools import lru_cache

from budget_cover import wheel_hitcover, wheel_maxcover
from covering.budget_climb import MIN_TICKETS, improve_budget_hits
from covering.common import (
    _coverage_pct,
    _greedy_fallback,
    _sorted_pool,
    lotto_coverage_pct,
    ensure_pool_numbers_on_tickets,
)
from covering.designs import (
    complete_design_size,
    wheel_lajolla,
    wheel_lotto,
    wheel_union34,
)
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


def budget_buys_complete_design(v: int, pick: int, guarantee: int, max_variants) -> bool:
    """Bugetul automat (hitcover) cuprinde designul complet validat.

    Atunci biletele sunt exact cele fără plafon: garanție 100%, cu cel mult
    atâtea bilete cât greedy-ul plafonat (care putea rămâne sub 100% la buget
    egal cu designul sau plăti bilete în plus). Comun dispatch-ului și
    semnăturii WF.
    """
    budget = int(max_variants or 0)
    if budget <= 0 or int(guarantee) >= int(pick):
        return False
    size = complete_design_size(int(v), int(pick), int(guarantee))
    return size is not None and size <= budget


@lru_cache(maxsize=16)
def _canonical_budget_start(v: int, pick: int, guarantee: int, max_variants: int, draw_n):
    """Pozițiile wheel-ului cu buget pe un pool fără scoruri (1 = cea mai bună).

    Punctul de plecare al căutării e același pentru orice pool cu aceeași
    geometrie, deci rezultatul ei se memorează o singură dată pe proces.
    """
    pool = list(range(1, v + 1))
    ordered = _sorted_pool(pool, None)
    wheel, _ = wheel_hitcover(pool, pick, guarantee, max_variants, None)
    wheel = ensure_pool_numbers_on_tickets(wheel, pool, pick)
    improved, audit = improve_hit_profile(ordered, wheel, draw_n=draw_n)
    if audit["applied"]:
        wheel = improved
    index = {n: i for i, n in enumerate(ordered)}
    return tuple(sum(1 << index[n] for n in t) for t in wheel)


def generate_wheel(
    method: str,
    pool,
    pick,
    guarantee,
    max_variants=0,
    scores=None,
    condition: int | None = None,
    draw_n: int | None = None,
    max_num: int | None = None,
):
    """Selectează algoritmul de wheeling. 'greedy' (sau necunoscut) → canonic.

    `condition` (numărul de numere din pool care trebuie să cadă ca garanția să
    se aplice) > `guarantee` comută pe lotto design „t dacă p" (`wheel_lotto`),
    indiferent de `method`: designurile locale și coverele clasice există doar
    pentru p == t. `condition` None sau egal cu garanția = cover clasic.
    Un cover clasic 4 complet, fără plafon, este rafinat pentru hituri 5+ când
    se extrag 6 numere. `draw_n` omis păstrează geometria `pick`; 5/40 transmite
    explicit 6. Metoda greedy explicită păstrează construcția de referință.

    `max_num` (dat de motor) activează rafinările bugetului automat (hitcover):
    coverul complet când designul validat încape în buget; altfel, peste 64 de
    bilete, `budget_climb`, acceptat numai cu dominanță exactă de profil. Fără
    `max_num` (biletele fizice) ieșirea rămâne cea de dinainte.
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
    if (
        fn is wheel_hitcover
        and max_num
        and budget_buys_complete_design(len(set(pool)), pick, guarantee, max_variants)
    ):
        return generate_wheel(
            "lajolla", pool, pick, guarantee, 0, scores, None, draw_n, max_num
        )
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
    if (
        fn is wheel_hitcover
        and max_num
        and int(max_variants or 0) >= MIN_TICKETS
        and cov < 100.0
        and len(set(pool)) == len(pool) <= 16
    ):
        draws = int(pick) if draw_n is None else int(draw_n)
        start = _canonical_budget_start(
            len(pool), int(pick), int(guarantee), int(max_variants), draw_n
        )
        improved, audit = improve_budget_hits(
            _sorted_pool(pool, scores),
            wheel,
            int(guarantee),
            draws,
            int(max_num),
            start=start,
        )
        if audit["applied"]:
            wheel = improved
            cov = _coverage_pct(wheel, pool, guarantee)
    return wheel, cov
