"""Selecție de pool din scoruri — top-N pur (aliniat cu bench).

Logică pură CPU. Bench-ul (`runner._top_k`) evaluează metodele pe
pool = top-K după scor. Producția trebuie să folosească ACEEAȘI regulă, altfel
câștigătorul pe rata 3+ din bench nu se reproduce în generare. Ambele deleagă
la regula canonică `core.ranking.rank_by_score` (scor desc, număr desc).

Diversificarea empirică decade/paritate a fost scoasă (2026-07): introducea
divergență față de metrică și față de decizia Auto-Pilot pe 3+. Tie-break-ul
pe frecvență din ``draw_matrix`` a fost scos și el (2026-07): bench-ul nu îl
aplica, deci la scoruri egale pool-ul generat DIVERGA de cel validat.

Singura abatere de la top-N este OPȚIUNEA utilizatorului ``max_consecutive_run``
(cerere 2026-09-26: fără trei numere consecutive în pool). Nu e o metodă și nu
schimbă scorurile: clasamentul se parcurge în ordine, iar numărul care ar forma
a treia secvență este înlocuit cu următorul care încape (`apply_consecutive_limit`).
Bench-ul rămâne pe scorerul brut; walk-forward-ul aplică aceeași regulă.
Fără avantaj statistic demonstrat: un pool de mărime fixă are aceeași
probabilitate hipergeometrică de hit oricare i-ar fi componența.
"""

from __future__ import annotations

import numpy as np

from loto_enterprise.core.ranking import (
    is_consecutive_block,
    limit_consecutive_run,
    longest_consecutive_run,
    rank_by_score,
)


def apply_consecutive_limit(
    ranked_all: list[int],
    pool_size: int,
    max_consecutive_run: int = 0,
    audit: dict | None = None,
) -> list[int]:
    """Primele ``pool_size`` numere din clasament, cu limita de consecutive.

    ``max_consecutive_run`` 0 = oprit (top-N pur, fără cheie în audit); 2 = fără
    trei consecutive. Pool-ul întors e în ordinea rangului. În audit,
    ``consecutive_limit`` spune ce număr a ieșit și ce număr a intrat, cu rangul
    fiecăruia în clasamentul metodei, și dacă limita a trebuit relaxată (bază
    restrânsă prea îngustă pentru pool). Liste, nu dicturi cu chei int: raportul
    și emailul trec prin JSON.
    """
    k = max(0, int(pool_size))
    top_n = ranked_all[:k]
    limit = max(0, int(max_consecutive_run or 0))
    if limit <= 0:
        if audit is not None:
            audit.pop("consecutive_limit", None)
        return top_n
    if longest_consecutive_run(top_n) <= limit:
        pool, applied = top_n, limit
    else:
        pool, applied, _skipped = limit_consecutive_run(ranked_all, k, limit)
    if audit is not None:
        rank = {n: i + 1 for i, n in enumerate(ranked_all)}
        audit["consecutive_limit"] = {
            "requested": limit,
            "applied": applied,
            "relaxed": applied != limit,
            "removed": [[n, rank[n]] for n in sorted(set(top_n) - set(pool))],
            "added": [[n, rank[n]] for n in sorted(set(pool) - set(top_n))],
        }
    return pool


def select_pool_from_scores(
    scores: dict[int, float],
    pool_size: int,
    blacklist: set[int],
    audit: dict | None = None,
    max_num: int = 49,
    draw_matrix: np.ndarray | None = None,
    max_consecutive_run: int = 0,
) -> list[int]:
    """Selectează top ``pool_size`` numere după scor (fără filtre de diversitate).

    Regula canonică `rank_by_score`: la scoruri egale, număr descrescător —
    IDENTIC cu bench `runner._top_k` (evită degenerarea „1,2,3…K” / „cele mai
    mici compuse”). ``draw_matrix`` rămâne în semnătură pentru compatibilitate
    cu apelantul din loto_engine, dar NU mai influențează selecția.
    ``max_consecutive_run`` > 0 aplică opțiunea utilizatorului descrisă în
    docstring-ul modulului; 0 lasă top-N pur, bit cu bit ca înainte.
    """
    valid = {
        int(n): float(s)
        for n, s in scores.items()
        if int(n) not in blacklist and 1 <= int(n) <= max_num
    }
    ranked_all = rank_by_score(valid, len(valid))
    pool = apply_consecutive_limit(ranked_all, pool_size, max_consecutive_run, audit)

    if audit is not None:
        n_unique = len({round(s, 9) for s in valid.values()})
        # Ordinea cheilor e clasamentul exact; `full_ticket` o citeste ca atare,
        # fiindca rotunjirea poate egala doua scoruri apropiate. Cu limita de
        # consecutive, un membru al pool-ului poate sta sub rangul 25: lista
        # merge atunci pana la el, ca `full_ticket` sa-l gaseasca.
        rank = {n: i + 1 for i, n in enumerate(ranked_all)}
        depth = max(25, max((rank[n] for n in pool), default=0))
        audit["timesfm_predictions"] = {
            n: round(valid[n], 6) for n in ranked_all[:depth]
        }
        if pool != ranked_all[: max(0, int(pool_size))]:
            audit["pool_selection"] = "top_score_max_run"
            audit["pool_selection_note"] = (
                "top-N după scor, cu limita de consecutive a utilizatorului "
                "(numărul care ar forma secvența e înlocuit cu următorul din clasament)"
            )
        else:
            audit["pool_selection"] = "top_score_pure"
            audit["pool_selection_note"] = (
                "top-N după scor (regulă canonică ranking, aliniat bench / țintă 3+)"
            )
        audit["pool_score_unique_levels"] = n_unique
        if n_unique < max(3, int(pool_size) // 2):
            audit["pool_selection_warning"] = (
                f"scorer cu doar {n_unique} nivele distincte — tie-break canonic (număr desc)"
            )
        run = longest_consecutive_run(pool)
        audit["pool_longest_consecutive_run"] = run
        block = is_consecutive_block(pool, min_size=6)
        audit["pool_is_consecutive_block"] = block
        if block:
            lo, hi = min(pool), max(pool)
            audit["pool_consecutive_warning"] = (
                f"pool-ul e un bloc consecutiv {lo}–{hi} ({len(pool)} numere) — "
                "scorer degenerat pe axa valorilor, nu semnal"
            )

    return sorted(pool)
