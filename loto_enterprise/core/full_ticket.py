"""Bilete fizice complete per joc (1-10), din pool-ul rezultatului afisat.

Numarul de variante simple de pe un bilet: 3 la 6/49, 4 la 5/40, 2 la Joker;
N bilete au de N ori mai multe. Variantele se aleg cu wheel-ul cu buget (acelasi
traseu ca `max_variants` in productie), deci acoperirea e recalculata exact
pentru aceste variante. Cand garantia e completa inainte de a umple biletele,
locurile ramase acopera grupe mai mari din acelasi pool (4 din 4, apoi 5 din 5),
tot cu wheel-ul greedy; niciun numar nu vine din afara pool-ului biletului.

Pool-ul biletului se potriveste automat pe clasamentul metodei, in aceeasi
ordine ca pool-ul afisat:
- prea mic pentru variante distincte (6/49 cu 6 numere are o singura
  combinatie de 6) -> se adauga urmatoarele numere din clasament;
- mai mare decat locurile de pe bilet (Joker: 2 x 5 = 10) -> raman cele mai
  bine clasate numere, ca niciun numar jucat sa nu fie ales la intamplare.
Clasamentul vine din `audit["timesfm_predictions"]`, calculat dupa
restrangerea bazei si dupa penalizarea recenta, deci le respecta pe amandoua.
`select_pool_from_scores` scrie acolo cel putin primele 25 de numere in ordinea
`rank_by_score` pe scorurile exacte, iar valorile rotunjite la 6 zecimale.
Ordinea cheilor ESTE clasamentul; nu se reordoneaza dupa valori: doua scoruri
apropiate devin egale dupa rotunjire, iar tie-break-ul canonic ar alege atunci
numarul mai mare, nu pe cel clasat de metoda.

Daca rezultatul a fost generat cu limita de consecutive a utilizatorului
(`audit["consecutive_limit"]`), extinderea o respecta: numarul care ar forma
secventa e sarit, iar nota il numeste. Rezultatele fara limita raman neschimbate.
"""

from __future__ import annotations

from math import comb

from covering.common import compute_coverage_pct
from covering.dispatch import generate_wheel
from loto_enterprise.core.ranking import longest_consecutive_run

TICKET_VARIANTS = {"6/49": 3, "5/40": 4, "joker": 2}  # variante pe un bilet fizic
PICK = {"6/49": 6, "5/40": 5, "joker": 5}
MAX_TICKETS = 10


def clamp_tickets(value) -> int:
    """Numarul de bilete cerut, intre 1 si 10 (campul gol sau text -> 1)."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 1
    return max(1, min(MAX_TICKETS, n))


def _pool_scores(data: dict) -> dict[int, float] | None:
    audit = data.get("audit") or {}
    raw = audit.get("ranking_scores_top25") or audit.get("timesfm_predictions")
    if not isinstance(raw, dict) or not raw:
        return None
    try:
        return {int(k): float(v) for k, v in raw.items()}
    except (TypeError, ValueError):
        return None


def _max_run(data: dict) -> int:
    """Limita de consecutive a rezultatului afisat (0 = generat fara limita)."""
    cl = (data.get("audit") or {}).get("consecutive_limit") or {}
    try:
        return max(0, int(cl.get("applied") or 0))
    except (TypeError, ValueError):
        return 0


def _ranked(scores: dict[int, float], among) -> list[int]:
    """Numerele din `among`, in ordinea clasamentului scris in audit."""
    return [n for n in scores if n in among]


def _min_pool(pick: int, n_var: int) -> int:
    """Cel mai mic pool din care ies `n_var` variante distincte de `pick`."""
    k = pick
    while comb(k, pick) < n_var:
        k += 1
    return k


def _fmt(nums) -> str:
    return ", ".join(str(n) for n in nums)


def _ticket_pool(
    pool: list[int],
    scores: dict[int, float] | None,
    n_var: int,
    pick: int,
    max_run: int = 0,
) -> tuple[list[int], str | None]:
    """Pool-ul efectiv al biletului si explicatia ajustarii (None = neschimbat)."""
    need = _min_pool(pick, n_var)
    capacity = n_var * pick
    if len(pool) < need:
        if not scores:
            return pool, (
                f"Pool-ul are {len(pool)} numere, prea puține pentru {n_var} "
                "variante distincte, iar clasamentul metodei lipsește din "
                "rezultat. Generați cu un pool mai mare."
            )
        # Limita nu poate fi mai stricta decat pool-ul afisat (rezultat relaxat).
        limit = max(max_run, longest_consecutive_run(pool)) if max_run else 0
        extra: list[int] = []
        skipped: list[int] = []
        for n in _ranked(scores, set(scores) - set(pool)):
            if len(extra) == need - len(pool):
                break
            if limit and longest_consecutive_run(pool + extra + [n]) > limit:
                skipped.append(n)
                continue
            extra.append(n)
        if len(extra) < need - len(pool):
            return pool, (
                f"Pool-ul are {len(pool)} numere, prea puține pentru {n_var} "
                "variante distincte."
            )
        which = (
            "următorul număr din clasamentul metodei"
            if len(extra) == 1
            else "următoarele numere din clasamentul metodei"
        )
        why = ""
        if skipped:
            why = (
                f" care nu formează {limit + 1} consecutive "
                f"(am sărit {_fmt(skipped)})"
            )
        return sorted(pool + extra), (
            f"Cu {len(pool)} numere există prea puține combinații de {pick} "
            f"pentru {n_var} variante; am adăugat {_fmt(sorted(extra))}, {which}{why}."
        )
    if len(pool) > capacity:
        if not scores or any(n not in scores for n in pool):
            return pool, (
                f"Pe {n_var} variante încap {capacity} numere, iar pool-ul are "
                f"{len(pool)}; clasamentul metodei lipsește, deci unele numere "
                "pot rămâne în afara biletului."
            )
        kept = _ranked(scores, set(pool))[:capacity]
        dropped = sorted(set(pool) - set(kept))
        return sorted(kept), (
            f"Pe {n_var} variante încap {capacity} numere; am păstrat cele mai "
            f"bine clasate {capacity}. În afara biletului: {_fmt(dropped)}."
        )
    return pool, None


def _fill_variants(
    pool: list[int], pick: int, guarantee: int, n_var: int, scores
) -> tuple[list[list[int]], int]:
    """Variantele biletelor si cate dintre ele formeaza wheel-ul garantiei.

    Wheel-ul se opreste cand garantia e completa. Locurile ramase se umplu cu
    wheel-ul garantiei urmatoare (g+1, apoi g+2 ... pana la sistemul complet),
    sarind variantele deja alese; pool-ul are cel putin `n_var` combinatii
    (`_min_pool`), deci biletele se umplu mereu cand clasamentul exista."""
    base, _ = generate_wheel(
        "greedy",
        pool=pool,
        pick=pick,
        guarantee=guarantee,
        max_variants=n_var,
        scores=scores,
    )
    chosen = [tuple(sorted(int(x) for x in v)) for v in base][:n_var]
    n_base = len(chosen)
    seen = set(chosen)
    level = guarantee
    while len(chosen) < n_var and level < pick:
        level += 1
        more, _ = generate_wheel(
            "greedy",
            pool=pool,
            pick=pick,
            guarantee=level,
            max_variants=n_var,
            scores=scores,
        )
        for v in more:
            t = tuple(sorted(int(x) for x in v))
            if t not in seen:
                chosen.append(t)
                seen.add(t)
                if len(chosen) == n_var:
                    break
    return [list(v) for v in chosen], n_base


def build_full_ticket(game: str, data: dict, tickets: int = 1) -> dict:
    """{variants, coverage, guarantee, joker, pool, note, error, ...} pentru un joc.

    `tickets` = bilete fizice (1-10); variantele cerute = bilete x variante pe bilet."""
    per_ticket = TICKET_VARIANTS.get(game)
    pick = PICK.get(game)
    if per_ticket is None:
        return {"error": f"joc necunoscut: {game}"}
    tickets = clamp_tickets(tickets)
    n_var = per_ticket * tickets
    pool = sorted({int(x) for x in (data.get("hard_core") or [])})
    if len(pool) < pick:
        return {"error": "pool-ul afișat e mai mic decât un bilet"}
    scores = _pool_scores(data)
    pool, note = _ticket_pool(pool, scores, n_var, pick, _max_run(data))
    guarantee = max(1, min(int(data.get("guarantee") or 3), pick))
    variants, n_base = _fill_variants(pool, pick, guarantee, n_var, scores)
    coverage = compute_coverage_pct(variants, pool, guarantee)
    upper = None
    if len(variants) > n_base and guarantee < pick:
        upper = (guarantee + 1, compute_coverage_pct(variants, pool, guarantee + 1))
    joker = None
    if game == "joker":
        jk = data.get("hard_core_joker") or []
        if jk:
            joker = int(jk[0])
            variants = [v + [joker] for v in variants]
    return {
        "variants": variants,
        "coverage": float(coverage),
        "guarantee": guarantee,
        "joker": joker,
        "pool": pool,
        "note": note,
        "error": None,
        "tickets": tickets,
        "per_ticket": per_ticket,
        "requested": n_var,
        "guarantee_variants": n_base,
        "upper_coverage": upper,
    }
