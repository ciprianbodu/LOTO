"""Wheel method dispatcher."""

from __future__ import annotations

import os
from functools import lru_cache

from budget_cover import wheel_hitcover, wheel_maxcover
from covering.anneal_hits import MAX_TICKETS as ANNEAL_MAX_TICKETS
from covering.anneal_hits import improve_ticket_chances
from covering.budget_climb import MIN_TICKETS, improve_budget_hits
from covering.budget_designs import improve_with_budget_design
from covering.guarantee_portfolio import applies as _portfolio_applies
from covering.guarantee_portfolio import improve_with_lower_guarantees
from covering.common import (
    _coverage_pct,
    _greedy_fallback,
    _order_by_scores,
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
from covering.refined_covers import improve_with_refined_cover
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


def _improve_lotto(pool, pick, guarantee, condition, max_variants, scores, wheel, cov, draw_n, max_num):
    """Ramura „g dacă c” cu universul jocului (2026-10-16).

    Fără plafon: designul lotto rafinat offline (`covering_designs/refined/RL_*`),
    certificat. Cu plafon: wheel-ul clasic al motorului la aceeași garanție și
    același buget, păstrat numai dacă e certificat față de designul trunchiat
    (aceleași bilete distincte, niciun prag în scădere, câștig strict) și nu
    acoperă mai puține grupe de c numere cu g pe un bilet (counts[c][g]).
    """
    draws = int(pick) if draw_n is None else int(draw_n)
    ordered = _sorted_pool(pool, scores)
    if int(max_variants or 0) <= 0:
        improved, audit = improve_with_refined_cover(
            ordered, wheel, guarantee, draws, max_num, condition=condition
        )
        if audit["applied"]:
            return improved, lotto_coverage_pct(improved, pool, guarantee, condition)
        return wheel, cov
    from covering.anneal_hits import _certify
    from covering.probability import wheel_hit_profile

    classic, _ = generate_wheel(
        "hitcover", pool, pick, guarantee, max_variants, scores, None, draw_n, max_num
    )
    tickets = [sorted(int(n) for n in t) for t in wheel]
    candidate = [sorted(int(n) for n in t) for t in classic]
    if not tickets or len(candidate) != len(tickets):
        return wheel, cov
    why, _gain, _c = _certify(ordered, tickets, candidate, guarantee, draws, max_num)
    if why:
        return wheel, cov
    if wheel_hit_profile(ordered, candidate)[condition][guarantee] < wheel_hit_profile(
        ordered, tickets
    )[condition][guarantee]:
        return wheel, cov
    return candidate, lotto_coverage_pct(candidate, pool, guarantee, condition)


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
    """`_generate_wheel`; pe calea motorului (`max_num` și scoruri date) biletele
    ies în ordinea sumei scorurilor, ca după La Jolla: etapele care înlocuiesc
    wheel-ul (designul cu buget, recoacerea, coverul rafinat, swap-urile) își
    întorc candidatul în ordinea proprie, iar UI-ul afișează primele N variante
    și costul „Top N bilete simple”. Mulțimea biletelor, acoperirea și hiturile
    nu se schimbă. Biletele fizice (fără `max_num`) își păstrează ordinea: baza
    garanției stă în primele variante."""
    wheel, cov = _generate_wheel(
        method, pool, pick, guarantee, max_variants, scores, condition, draw_n, max_num
    )
    if max_num and scores and wheel:
        wheel = _order_by_scores(wheel, scores)
    return wheel, cov


def _generate_wheel(
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
    bilete, `budget_climb`, acceptat numai cu dominanță exactă de profil, iar
    la 1..64 de bilete recoacerea pe șansa exactă (`covering.anneal_hits`),
    acceptată numai dacă niciun prag afișat nu scade. Fără `max_num`
    (biletele fizice) ieșirea rămâne cea de dinainte. Tot cu `max_num`, fără
    plafon, coverul La Jolla e înlocuit de coverul rafinat al geometriei
    jocului (`covering.refined_covers`), numai certificat: aceeași garanție
    100%, aceleași bilete ca număr, niciun prag în scădere, câștig strict.
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
        if max_num and len(set(pool)) == len(pool) <= 16:
            wheel, cov = _improve_lotto(
                pool, pick, int(guarantee), int(condition), max_variants, scores,
                wheel, cov, draw_n, int(max_num),
            )
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
        fn is wheel_lajolla
        and max_num
        and int(max_variants or 0) == 0
        and cov >= 100.0
        and len(set(pool)) == len(pool) <= 16
    ):
        # Coverul complet rafinat offline pentru pragul de peste garanție
        # (`covering_designs/refined/`), certificat față de wheel-ul de mai sus.
        draws = int(pick) if draw_n is None else int(draw_n)
        improved, audit = improve_with_refined_cover(
            _sorted_pool(pool, scores), wheel, int(guarantee), draws, int(max_num)
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
        # Designul cu buget precalculat offline (`covering_designs/budget/`),
        # certificat exact față de wheel-ul de mai sus (2026-10-12).
        improved, audit = improve_with_budget_design(
            _sorted_pool(pool, scores), wheel, int(guarantee), draws, int(max_num)
        )
        if audit["applied"]:
            wheel = improved
            cov = _coverage_pct(wheel, pool, guarantee)
    if (
        fn is wheel_hitcover
        and max_num
        and 1 <= int(max_variants or 0) <= ANNEAL_MAX_TICKETS
        and cov < 100.0
        and len(set(pool)) == len(pool) <= 16
    ):
        draws = int(pick) if draw_n is None else int(draw_n)
        improved, audit = improve_ticket_chances(
            _sorted_pool(pool, scores), wheel, int(guarantee), draws, int(max_num)
        )
        if audit["applied"]:
            wheel = improved
            cov = _coverage_pct(wheel, pool, guarantee)
    if (
        fn is wheel_hitcover
        and max_num
        and int(max_variants or 0) > 0
        and cov < 100.0
        and len(set(pool)) == len(pool)
        and _portfolio_applies(len(pool), int(pick), int(guarantee), len(wheel))
    ):
        # Garanția egală cu biletul, cu plafon: biletele de la o garanție mai
        # mică, certificate pe garanția cerută (`covering.guarantee_portfolio`).
        draws = int(pick) if draw_n is None else int(draw_n)
        improved, audit = improve_with_lower_guarantees(
            _sorted_pool(pool, scores), wheel, int(guarantee), draws, int(max_num)
        )
        if audit["applied"]:
            wheel = improved
            cov = _coverage_pct(wheel, pool, guarantee)
    return wheel, cov
