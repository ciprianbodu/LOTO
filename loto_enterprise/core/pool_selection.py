"""Selecție de pool din scoruri — top-N pur (aliniat cu bench).

Logică pură CPU. Bench-ul (`runner._top_k`) evaluează metodele pe
pool = top-K după scor. Producția trebuie să folosească ACEEAȘI regulă, altfel
câștigătorul pe rata 3+ din bench nu se reproduce în generare. Ambele deleagă
la regula canonică `core.ranking.rank_by_score` (scor desc, număr desc).

Diversificarea empirică decade/paritate a fost scoasă (2026-07): introducea
divergență față de metrică și față de decizia Auto-Pilot pe 3+. Tie-break-ul
pe frecvență din ``draw_matrix`` a fost scos și el (2026-07): bench-ul nu îl
aplica, deci la scoruri egale pool-ul generat DIVERGA de cel validat.
"""

from __future__ import annotations

import numpy as np

from loto_enterprise.core.ranking import is_finite_score, rank_by_score


def select_pool_from_scores(
    scores: dict[int, float],
    pool_size: int,
    blacklist: set[int],
    audit: dict | None = None,
    max_num: int = 49,
    draw_matrix: np.ndarray | None = None,
) -> list[int]:
    """Selectează top ``pool_size`` numere după scor (fără filtre de diversitate).

    Regula canonică `rank_by_score`: la scoruri egale, număr descrescător —
    IDENTIC cu bench `runner._top_k` (evită degenerarea „1,2,3…K” / „cele mai
    mici compuse”). ``draw_matrix`` rămâne în semnătură pentru compatibilitate
    cu apelantul din loto_engine, dar NU mai influențează selecția.
    """
    valid = {}
    for n, s in scores.items():
        ni = int(n)
        if ni in blacklist or ni < 1 or ni > max_num:
            continue
        if not is_finite_score(s):
            continue
        valid[ni] = float(s)
    ranked_all = rank_by_score(valid, len(valid))
    pool = ranked_all[: max(0, int(pool_size))]

    if audit is not None:
        n_unique = len({round(s, 9) for s in valid.values()})
        audit["timesfm_predictions"] = {n: round(valid[n], 6) for n in ranked_all[:25]}
        audit["pool_selection"] = "top_score_pure"
        audit["pool_selection_note"] = "top-N după scor (regulă canonică ranking, aliniat bench / țintă 3+)"
        audit["pool_score_unique_levels"] = n_unique
        if n_unique < max(3, int(pool_size) // 2):
            audit["pool_selection_warning"] = (
                f"scorer cu doar {n_unique} nivele distincte — tie-break canonic (număr desc)"
            )

    return sorted(pool)
