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


def complete_pool(
    pool: list[int],
    pool_size: int,
    *,
    max_num: int,
    scores: dict | None = None,
    exclude: set[int] | None = None,
    freq=None,
    allow_excluded_last_resort: bool = False,
) -> list[int]:
    """Umple ``pool`` până la ``pool_size`` fără (cât e posibil) numere din ``exclude``.

    Folosit când scorurile sunt rare / NaN sau blacklist-ul a tăiat prea mult:
    1. scoruri finite permise;
    2. frecvență / universul 1..max_num, tot în afara ``exclude``;
    3. opțional last-resort din ``exclude`` (doar ca K să nu scadă — enforcement-ul
       manual poate scoate apoi aceste numere).

    Pool deja complet → aceeași apartenență, sortat numeric (ca
    ``select_pool_from_scores``). Nu e o a doua regulă de top-N: completează
    doar golurile, cu ``rank_by_score``.
    """
    pool_size = max(0, int(pool_size))
    max_num = int(max_num)
    seen: set[int] = set()
    out: list[int] = []
    for n in pool:
        try:
            ni = int(n)
        except (TypeError, ValueError):
            continue
        if ni < 1 or ni > max_num or ni in seen:
            continue
        out.append(ni)
        seen.add(ni)
        if len(out) >= pool_size:
            return sorted(out[:pool_size])

    exclude_set = {int(x) for x in (exclude or set())}

    def _freq_dict() -> dict[int, float]:
        if freq is None:
            return {}
        if isinstance(freq, dict):
            return {int(k): float(v) for k, v in freq.items() if is_finite_score(v)}
        return {
            i + 1: float(freq[i])
            for i in range(len(freq))
            if is_finite_score(freq[i])
        }

    def _take(cands: dict[int, float], banned: set[int]) -> None:
        if len(out) >= pool_size or not cands:
            return
        for n in rank_by_score(cands, len(cands)):
            if n in seen or n in banned or n < 1 or n > max_num:
                continue
            out.append(n)
            seen.add(n)
            if len(out) >= pool_size:
                return

    if scores:
        from_scores = {
            int(n): float(s)
            for n, s in scores.items()
            if is_finite_score(s)
            and 1 <= int(n) <= max_num
            and int(n) not in seen
            and int(n) not in exclude_set
        }
        _take(from_scores, exclude_set)

    if len(out) < pool_size:
        fd = _freq_dict()
        rest = {
            n: fd[n] if n in fd else 0.0
            for n in range(1, max_num + 1)
            if n not in seen and n not in exclude_set
        }
        _take(rest, exclude_set)

    if len(out) < pool_size and allow_excluded_last_resort and exclude_set:
        fd = _freq_dict()
        last: dict[int, float] = {}
        if scores:
            for n, s in scores.items():
                ni = int(n)
                if ni in exclude_set and ni not in seen and 1 <= ni <= max_num and is_finite_score(s):
                    last[ni] = float(s)
        for n in exclude_set:
            if n not in seen and 1 <= n <= max_num and n not in last:
                last[n] = fd.get(n, 0.0)
        _take(last, set())

    return sorted(out[:pool_size])
