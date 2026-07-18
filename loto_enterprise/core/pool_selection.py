"""Selecție de pool din scoruri — top-N pur (aliniat cu bench).

Logică pură CPU (numpy). Bench-ul (`runner._top_k`) evaluează metodele pe
pool = top-K după scor. Producția trebuie să folosească ACEEAȘI regulă, altfel
câștigătorul pe rata 3+ din bench nu se reproduce în generare.

Diversificarea empirică decade/paritate a fost scoasă (2026-07): introducea
divergență față de metrică și față de decizia Auto-Pilot pe 3+.
"""

from __future__ import annotations

import numpy as np


def select_pool_from_scores(
    scores: dict[int, float],
    pool_size: int,
    blacklist: set[int],
    audit: dict | None = None,
    max_num: int = 49,
    draw_matrix: np.ndarray | None = None,
) -> list[int]:
    """Selectează top ``pool_size`` numere după scor (fără filtre de diversitate).

    ``draw_matrix`` e acceptat pentru compatibilitate API; nu influențează selecția.
    """
    del draw_matrix  # API compat; selecție = doar scor
    valid = {
        int(n): float(s)
        for n, s in scores.items()
        if int(n) not in blacklist and 1 <= int(n) <= max_num
    }
    sorted_nums = sorted(valid.items(), key=lambda x: x[1], reverse=True)
    pool = [n for n, _ in sorted_nums[: max(0, int(pool_size))]]

    if audit is not None:
        audit["timesfm_predictions"] = {n: round(s, 6) for n, s in sorted_nums[:25]}
        audit["pool_selection"] = "top_score_pure"
        audit["pool_selection_note"] = "top-N după scor (aliniat bench / țintă 3+)"

    return sorted(pool)
