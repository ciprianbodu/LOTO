"""Garanția egală cu biletul, cu plafon: candidați de la garanții mai mici (2026-10-16).

Cu garanția egală cu numerele de pe bilet (6/49 garanția 6, 5/40 și Joker
garanția 5) și un plafon de bilete, sistemul complet nu încape, iar greedy-ul
cu buget dă primele N combinații lexicografice ale pool-ului. Recoacerea pe
șansa exactă urmărește apoi P(garanție), care e aceeași pentru orice N bilete
distincte, deci pragurile mai mici rămân la voia întâmplării. La același pool
și același număr de bilete, wheel-ul construit la o garanție mai mică
(3..garanția-1) domină: 6/49 pool 16, 30 de bilete, 3+ 24,10% -> 27,04%.

Candidații se construiesc o singură dată pe geometrie, pe pozițiile canonice
(pool 1..v, scoruri descrescătoare), prin aceeași cale a motorului
(`generate_wheel("hitcover", ..., max_num)`), apoi se pun pe pozițiile
clasamentului și se certifică la fiecare apel față de wheel-ul livrat, pe
garanția CERUTĂ (`covering.anneal_hits._certify`): același număr de bilete
distincte, numerele jucate rămân jucate, niciun prag de la 3 la numărul extras
nu scade, câștig strict, acoperirea garanției nu scade (la garanția egală cu
biletul ea e chiar numărul de bilete). Dintre candidații certificați rămâne cel
mai bun pe ordinea garanție, apoi pragurile mai mici. Numai geometrie,
extrageri uniforme: rata pool-ului nu se schimbă.

Cu plafonul peste coverul complet al garanției mai mici, candidatul e acel cover,
cu mai puține bilete; din 2026-10-20 se completează până la plafon cu biletele
wheel-ului livrat (`_padded`), apoi se certifică.
"""

from __future__ import annotations

from functools import lru_cache

from covering.anneal_hits import MIN_THRESHOLD, _certify
from covering.probability import wheel_hit_probabilities

# 19: Loto 5/40 se joacă până la 19 numere (scheme LR Cod 45-48, 2026-10-22);
# șansa exactă merge până la 20. 6/49 și Joker rămân la cel mult 16.
MAX_POOL = 19


@lru_cache(maxsize=64)
def _canonical_candidate(
    v: int, pick: int, guarantee: int, budget: int, draw_n: int, max_num: int, _sources=()
):
    """Biletele motorului la garanția dată, pe poziții 1..v (1 = cel mai bine clasat).

    `_sources` (amprentele fișierelor C, R, H ale acestei geometrii) intră în
    cheia memoriei: un fișier schimbat în timpul procesului reface candidatul."""
    from covering.dispatch import generate_wheel

    pool = list(range(1, v + 1))
    scores = {n: float(v - i) for i, n in enumerate(pool)}
    wheel, _ = generate_wheel("hitcover", pool, pick, guarantee, budget, scores, None, draw_n, max_num)
    return tuple(tuple(sorted(int(n) for n in t)) for t in wheel)


def applies(v: int, pick: int, guarantee: int, budget: int) -> bool:
    """Ramura se aplică: garanția egală cu biletul, plafon, pool mic."""
    return int(guarantee) >= int(pick) >= 4 and 1 <= int(budget) and int(pick) < int(v) <= MAX_POOL


def _padded(candidate: list[list[int]], tickets: list[list[int]]) -> list[list[int]]:
    """Candidatul completat până la numărul de bilete al wheel-ului livrat.

    Cu plafonul cel puțin cât coverul complet al garanției mai mici, motorul dă
    acel cover, cu mai puține bilete decât plafonul, iar certificarea (același
    număr de bilete distincte) îl respingea: 5/40 pool 16, garanția 5, plafonul
    404 dădea 4+ 16,03%, plafonul 405 numai 8,30%. Biletele lipsă vin din
    wheel-ul livrat, în ordinea lui, fără dubluri. Un bilet în plus nu scade
    șansa de a prinde t pe o variantă la niciun prag, deci candidatul completat
    are cel puțin șansele coverului; certificarea decide față de wheel."""
    have = {tuple(t) for t in candidate}
    out = [list(t) for t in candidate]
    for t in tickets:
        if len(out) >= len(tickets):
            break
        if tuple(t) not in have:
            have.add(tuple(t))
            out.append(list(t))
    return out


def improve_with_lower_guarantees(ordered_pool, wheel, guarantee: int, draw_n: int, max_num: int):
    """``(wheel, audit)``: cel mai bun candidat certificat de la o garanție mai mică."""
    audit = {"applied": False, "reason": "", "source_guarantee": None, "gain": {}}
    pool = [int(n) for n in ordered_pool]
    tickets = [sorted(int(n) for n in t) for t in wheel]
    if not tickets:
        audit["reason"] = "no tickets"
        return wheel, audit
    pick = len(tickets[0])
    v = len(pool)
    if (
        not applies(v, pick, guarantee, len(tickets))
        or len(set(pool)) != v
        or any(len(t) != pick for t in tickets)
        or v >= int(max_num)
    ):
        audit["reason"] = "outside limits"
        return wheel, audit
    top = min(pick, int(draw_n))
    order = [int(guarantee), *range(int(guarantee) - 1, MIN_THRESHOLD - 1, -1)]
    order = [t for t in order if MIN_THRESHOLD <= t <= top]
    best, best_key, best_source = None, None, None
    for lower in range(pick - 1, MIN_THRESHOLD - 1, -1):
        from covering.dispatch import classic_sources

        sources = classic_sources(v, pick, lower, len(tickets), int(draw_n), int(max_num))
        positions = _canonical_candidate(
            v, pick, lower, len(tickets), int(draw_n), int(max_num), sources
        )
        candidate = _padded([sorted(pool[p - 1] for p in block) for block in positions], tickets)
        why, _gain, _cov = _certify(pool, tickets, candidate, int(guarantee), int(draw_n), int(max_num))
        if why:
            continue
        p = wheel_hit_probabilities(pool, candidate, int(draw_n), int(max_num))["ticket"]
        key = tuple(p[t] for t in order)
        if best_key is None or key > best_key:
            best, best_key, best_source = candidate, key, lower
    if best is None:
        audit["reason"] = "no certified candidate"
        return wheel, audit
    before = wheel_hit_probabilities(pool, tickets, int(draw_n), int(max_num))["ticket"]
    after = wheel_hit_probabilities(pool, best, int(draw_n), int(max_num))["ticket"]
    audit.update(
        applied=True,
        source_guarantee=best_source,
        gain={t: after[t] - before[t] for t in range(MIN_THRESHOLD, top + 1)},
    )
    return best, audit
