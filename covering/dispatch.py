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
    _pool_rank_key,
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
from covering.multihit import wheel_multihit
from covering.weighted import wheel_weighted
from covering.profile_swap import improve_hit_profile
from covering.lr_schemes import MAX_POOL as _LR_MAX_POOL
from covering.lr_schemes import improve_with_lr_scheme
from covering.refined_covers import improve_with_refined_cover
from covering.search import wheel_annealing, wheel_genetic

WHEEL_METHODS = {
    # Experimental, explicit overrides only; automatic selection stays unchanged.
    "weighted": wheel_weighted,
    "multihit": wheel_multihit,
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


def classic_sources(v: int, pick: int, guarantee: int, budget: int, draw_n, max_num) -> tuple:
    """Amprentele fișierelor din care poate veni wheel-ul clasic al motorului la
    acest buget (C, R, H): intră în cheia memoriei candidaților canonici, ca un
    fișier schimbat în timpul procesului să nu servească un candidat vechi."""
    from covering.budget_designs import budget_design_signature
    from covering.designs import covering_design_source_signature
    from covering.refined_covers import refined_cover_signature

    draws = int(pick) if draw_n is None else int(draw_n)
    return (
        covering_design_source_signature(int(v), int(pick), int(guarantee)),
        refined_cover_signature(int(v), int(pick), int(guarantee), draws, int(max_num)),
        budget_design_signature(int(v), int(pick), int(guarantee), int(budget)),
    )


@lru_cache(maxsize=64)
def _canonical_classic(v: int, pick: int, guarantee: int, budget: int, draw_n, max_num: int, _sources):
    """Wheel-ul clasic al motorului pe poziții 1..v (1 = cel mai bine clasat)."""
    pool = list(range(1, v + 1))
    scores = {n: float(v - i) for i, n in enumerate(pool)}
    wheel, _ = generate_wheel("hitcover", pool, pick, guarantee, budget, scores, None, draw_n, max_num)
    return tuple(tuple(sorted(int(n) for n in t)) for t in wheel)


def _improve_lotto(pool, pick, guarantee, condition, max_variants, scores, wheel, cov, draw_n, max_num):
    """Ramura „g dacă c” cu universul jocului (2026-10-16).

    Candidații, fiecare certificat față de designul livrat (aceleași bilete
    distincte, niciun prag în scădere, câștig strict) și cu cel puțin atâtea
    grupe de c numere cu g pe un bilet (counts[c][g]):
    - designul lotto rafinat offline (`covering_designs/refined/RL_*`); intră
      când plafonul cuprinde designul întreg (altfel numărul de bilete diferă);
    - wheel-ul clasic al motorului la același număr de bilete: la plafonul
      care taie designul, construit pe scorurile pasului; altfel (fără plafon
      sau cu un plafon cât designul întreg), construit o dată pe geometrie, pe
      poziții canonice. Fără plafon, cu plafon cât designul sau mai mare, se
      livrează deci aceleași bilete.
    Dintre cei certificați rămâne cel mai bun pe pragul garanției, apoi pe cele
    mai mari, apoi pe cele mai mici.
    """
    from covering.anneal_hits import _certify
    from covering.designs import lotto_design_size
    from covering.probability import wheel_hit_probabilities, wheel_hit_profile

    draws = int(pick) if draw_n is None else int(draw_n)
    ordered = _sorted_pool(pool, scores)
    tickets = [sorted(int(n) for n in t) for t in wheel]
    if not tickets:
        return wheel, cov
    base_cover = wheel_hit_profile(ordered, tickets)[condition][guarantee]
    candidates = []
    improved, audit = improve_with_refined_cover(
        ordered, wheel, guarantee, draws, max_num, condition=condition
    )
    if audit["applied"]:
        candidates.append([sorted(int(n) for n in t) for t in improved])
    cap = int(max_variants or 0)
    full = lotto_design_size(len(ordered), int(pick), int(guarantee), int(condition))
    classic = None
    if cap > 0 and (full is None or cap < full):
        classic, _ = generate_wheel(
            "hitcover", pool, pick, guarantee, cap, scores, None, draw_n, max_num
        )
    elif full is not None and len(tickets) == full:
        sources = classic_sources(len(ordered), pick, guarantee, full, draw_n, max_num)
        positions = _canonical_classic(
            len(ordered), int(pick), int(guarantee), full, draw_n, int(max_num), sources
        )
        classic = [sorted(ordered[p - 1] for p in t) for t in positions]
    if classic is not None:
        candidate = [sorted(int(n) for n in t) for t in classic]
        if len(candidate) == len(tickets):
            why, _gain, _c = _certify(ordered, tickets, candidate, guarantee, draws, max_num)
            if not why and wheel_hit_profile(ordered, candidate)[condition][guarantee] >= base_cover:
                candidates.append(candidate)
    if not candidates:
        return wheel, cov
    top = min(int(pick), draws)
    order = [*range(int(guarantee), top + 1), *range(int(guarantee) - 1, 2, -1)]

    def key(t):
        p = wheel_hit_probabilities(ordered, t, draws, int(max_num))["ticket"]
        return tuple(p[k] for k in order if k >= 3)

    best = max(candidates, key=key)
    return best, lotto_coverage_pct(best, pool, guarantee, condition)


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
        order_scores = scores
        if (method or "").strip().lower() in {"weighted", "multihit"}:
            order_scores = {n: _pool_rank_key(n, scores)[0] for n in pool}
        wheel = _order_by_scores(wheel, order_scores)
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
    if fn in (wheel_weighted, wheel_multihit):
        # Generic hit-profile swaps preserve one-cover probabilities, but can
        # destroy multiplicity or the score-weighted objective of these APIs.
        return wheel, cov
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
    if (
        fn is wheel_hitcover
        and max_num
        and int(max_variants or 0) > 0
        and len(set(pool)) == len(pool) <= _LR_MAX_POOL
    ):
        # Schema redusă LR cu tot atâtea variante pe tot atâtea numere și bilet
        # de aceeași mărime (`covering_designs/lr/`, de la orice joc), certificată
        # față de wheel-ul de mai sus (2026-10-20); lista adoptărilor e în
        # `scripts/analysis/lr_optimality_2026-10-20.md`.
        draws = int(pick) if draw_n is None else int(draw_n)
        improved, audit = improve_with_lr_scheme(
            _sorted_pool(pool, scores), wheel, int(guarantee), draws, int(max_num)
        )
        if audit["applied"]:
            wheel = improved
            cov = _coverage_pct(wheel, pool, guarantee)
    return wheel, cov
