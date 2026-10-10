"""Bilete fizice complete per joc (1-10), din pool-ul rezultatului afisat.

Numarul de variante simple de pe un bilet: 3 la 6/49, 4 la 5/40, 2 la Joker;
N bilete au de N ori mai multe. Variantele se aleg cu wheel-ul cu buget (acelasi
traseu ca `max_variants` in productie, implicit hitcover), deci acoperirea e recalculata exact
pentru aceste variante. Cand garantia e completa inainte de a umple biletele,
locurile ramase acopera grupe mai mari din acelasi pool (g+1 din g+1, apoi pana
la sistemul complet), tot cu wheel-ul greedy; niciun numar nu vine din afara
pool-ului biletului.

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

`chances` da probabilitatea exacta (extragere uniforma) ca cel putin o
varianta afisata sa prinda t numere, la fiecare prag cu premiu.
"""

from __future__ import annotations

from math import comb

from covering.common import compute_coverage_pct
from covering.dispatch import generate_wheel, resolve_wheel_method
from covering.spread import ticket_hit_probabilities
from loto_enterprise.core.ranking import limit_consecutive_run, longest_consecutive_run
from loto_enterprise.core.ro_text import count

TICKET_VARIANTS = {"6/49": 3, "5/40": 4, "joker": 2}  # variante pe un bilet fizic
PICK = {"6/49": 6, "5/40": 5, "joker": 5}
MAX_TICKETS = 10


def clamp_tickets(value) -> int:
    """Numarul de bilete cerut, intre 1 si 10 (campul gol sau text -> 1)."""
    try:
        n = int(round(float(value)))  # ca ui.number(precision=0), nu trunchiere
    except (TypeError, ValueError, OverflowError):
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
    """Limita de consecutive aplicata pool-ului afisat (0 = generat fara limita).

    Limita aplicata, nu cea ceruta: pe o baza restransa ea se relaxeaza ca
    pool-ul sa incapa."""
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
    tickets: int = 1,
) -> tuple[list[int], str | None]:
    """Pool-ul efectiv al biletelor si explicatia ajustarii (None = neschimbat)."""
    slip = "biletului" if tickets == 1 else "biletelor"
    need = _min_pool(pick, n_var)
    capacity = n_var * pick
    if len(pool) < need:
        if not scores:
            return pool, (
                f"Pool-ul are {len(pool)} numere, prea puține pentru "
                f"{count(n_var, 'variante')} distincte, iar clasamentul metodei "
                "lipsește din rezultat. Generați cu un pool mai mare."
            )
        others = _ranked(scores, set(scores) - set(pool))
        # Pool-ul afișat rămâne întreg; completarea vine din clasament, în ordine.
        cands = _ranked(scores, set(pool)) + [n for n in pool if n not in scores] + others
        target = min(need, len(cands))
        # Limita nu poate fi mai strictă decât pool-ul afișat (rezultat relaxat).
        limit = max(max_run, longest_consecutive_run(pool)) if max_run else 0
        chosen, applied = cands[:target], limit
        if limit:
            # Același parcurs cu verificare de completare ca în producție; limita
            # crește numai dacă altfel s-ar pierde un număr din pool-ul afișat.
            for lim in range(limit, target + 1):
                sel, applied, _ = limit_consecutive_run(cands, target, lim)
                if set(pool) <= set(sel):
                    chosen = sel
                    break
        extra = sorted(set(chosen) - set(pool))
        if not extra:
            return pool, (
                f"Pool-ul are {len(pool)} numere, prea puține pentru "
                f"{count(n_var, 'variante')} distincte, iar clasamentul nu are alte numere."
            )
        last = max(others.index(n) for n in extra)
        skipped = [n for n in others[:last] if n not in chosen]
        which = (
            "următorul număr din clasamentul metodei"
            if len(extra) == 1
            else "următoarele numere din clasamentul metodei"
        )
        why = ""
        if skipped:
            why = (
                f" care nu formează {applied + 1} consecutive "
                f"(am sărit {_fmt(skipped)})"
            )
        note = (
            f"Cu {len(pool)} numere există prea puține combinații de {pick} "
            f"pentru {count(n_var, 'variante')}; am adăugat {_fmt(extra)}, {which}{why}."
        )
        if limit and applied > limit:
            note += (
                f" Limita de consecutive a crescut la {applied}: altfel nu exista "
                "o completare care să păstreze tot pool-ul afișat."
            )
        if target < need:
            note += (
                f" Clasamentul are numai {len(cands)} numere, deci ies cel mult "
                f"{count(comb(target, pick), 'variante')} distincte."
            )
        return sorted(chosen), note
    if len(pool) > capacity:
        if not scores or any(n not in scores for n in pool):
            return pool, (
                f"Pe {count(n_var, 'variante')} încap {count(capacity, 'numere')}, iar pool-ul are "
                f"{len(pool)}; clasamentul metodei lipsește, deci unele numere "
                f"pot rămâne în afara {slip}."
            )
        kept = _ranked(scores, set(pool))[:capacity]
        dropped = sorted(set(pool) - set(kept))
        return sorted(kept), (
            f"Pe {count(n_var, 'variante')} încap {count(capacity, 'numere')}; am păstrat "
            f"cele mai bine clasate {capacity}. În afara {slip}: {_fmt(dropped)}."
        )
    return pool, None


def _fill_variants(
    pool: list[int],
    pick: int,
    guarantee: int,
    n_var: int,
    scores,
    *,
    draw_n: int | None = None,
) -> tuple[list[list[int]], int]:
    """Variantele biletelor si cate dintre ele formeaza wheel-ul garantiei.

    Wheel-ul se opreste cand garantia e completa. Locurile ramase se umplu cu
    wheel-ul garantiei urmatoare (g+1, apoi g+2 ... pana la sistemul complet),
    sarind variantele deja alese. Biletele se umplu cand pool-ul are cel putin
    `n_var` combinatii (`_min_pool`), adica atunci cand clasamentul are destule
    numere; altfel ies toate combinatiile pool-ului.

    Rafinarea pastreaza baza garantiei si incepe cu geometria `pick` deja
    livrata. Daca `draw_n` e mai mare, urmeaza o rafinare suplimentara numai
    pe locurile ramase, cu profil nedescrescator fata de rezultatul livrat.
    """
    base, _ = generate_wheel(
        resolve_wheel_method(n_var),
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
    if n_base < len(chosen):
        # Leftover slots only: the base wheel (first n_base) stays intact.
        from covering.common import _sorted_pool
        from covering.profile_swap import improve_hit_profile

        # Preserve the delivered pick-number search before refining the real
        # draw geometry. Replacing it outright can follow a different local
        # optimum, with a lower profile than the previously delivered wheel.
        geometries = [pick]
        if draw_n is not None and int(draw_n) > pick:
            geometries.append(int(draw_n))
        for drawn in geometries:
            improved, audit = improve_hit_profile(
                _sorted_pool(pool, scores), chosen, draw_n=drawn, frozen=n_base
            )
            if audit["applied"]:
                chosen = improved
    return [list(v) for v in chosen], n_base


def chance_thresholds(min_target: int, pick: int, draw_n: int) -> list[int]:
    """Pragurile afisate; marele premiu (t = pick = draw_n) e acelasi pentru orice aranjare."""
    return [t for t in range(max(1, min_target), pick + 1) if not (t == pick == draw_n)]


def build_full_ticket(
    game: str,
    data: dict,
    tickets: int = 1,
    *,
    with_chances: bool = True,
) -> dict:
    """{variants, coverage, guarantee, joker, pool, note, error, ...} pentru un joc.

    `tickets` = bilete fizice (1-10); variantele cerute = bilete x variante pe bilet.
    `with_chances=False` lasa `chances` None: enumerarea tuturor extragerilor
    costa cat restul constructiei."""
    from loto_enterprise.core.lotteries import lottery_by_id

    lot = lottery_by_id(game)
    per_ticket = TICKET_VARIANTS.get(game)
    pick = PICK.get(game)
    is_joker = game == "joker"
    if per_ticket is None:
        # Jocurile din registru (alte țări): variante pe bilet și geometrie de
        # acolo; un bilet neverificat (`per_ticket=None`) nu se inventează.
        if lot is None:
            return {"error": f"joc necunoscut: {game}"}
        if lot.per_ticket is None:
            return {"error": f"bilet nemodelat: {lot.display}"}
        per_ticket, pick = int(lot.per_ticket), int(lot.pick_n)
        second = lot.geo.second
        is_joker = bool(second and second.modelled and second.draw_n == 1)
    tickets = clamp_tickets(tickets)
    n_var = per_ticket * tickets
    shown_pool = sorted({int(x) for x in (data.get("hard_core") or [])})
    if len(shown_pool) < pick:
        return {"error": "pool-ul afișat e mai mic decât un bilet"}
    scores = _pool_scores(data)
    max_run = _max_run(data)
    guarantee = max(1, min(int(data.get("guarantee") or 3), pick))
    draw_n = lot.draw_n if lot is not None else pick
    max_num = lot.max_n if lot is not None else max(shown_pool)
    min_target = lot.min_hit_target if lot is not None else 3

    pool, note = _ticket_pool(shown_pool, scores, n_var, pick, max_run, tickets)
    plain, n_base = _fill_variants(pool, pick, guarantee, n_var, scores, draw_n=draw_n)
    upper = None
    if len(plain) > n_base and guarantee < pick:
        upper = (guarantee + 1, compute_coverage_pct(plain, pool, guarantee + 1))
    thresholds = chance_thresholds(min_target, pick, draw_n)
    chances = None
    try:
        shown = (
            ticket_hit_probabilities(plain, draw_n, max_num)
            if with_chances
            else None
        )
    except ValueError:
        shown = None  # numere in afara universului jocului: fara sanse exacte
    if shown is not None:
        chances = {"thresholds": thresholds, "shown": {t: shown[t] for t in thresholds}}
    variants = plain
    joker = None
    if is_joker:
        jk = data.get("hard_core_joker") or []
        if jk:
            joker = int(jk[0])
            variants = [v + [joker] for v in variants]
    return {
        "variants": variants,
        "coverage": float(compute_coverage_pct(plain, pool, guarantee)),
        "pool": pool,
        "note": note,
        "guarantee_variants": n_base,
        "upper_coverage": upper,
        "guarantee": guarantee,
        "joker": joker,
        "error": None,
        "tickets": tickets,
        "per_ticket": per_ticket,
        "requested": n_var,
        "chances": chances,
    }
