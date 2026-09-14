"""Shared covering helpers. Public API stays on wheeling_methods."""
from __future__ import annotations

import itertools
import math
from collections import Counter

import numpy as np

logger = __import__('logging').getLogger(__name__)


def _comb(n: int, k: int) -> int:
    return math.comb(n, k) if 0 <= k <= n else 0


def _greedy_fallback(pool, pick, guarantee, max_variants, scores):
    """Lazy canonical greedy (avoids import cycle)."""
    from covering.greedy import generate_combinatorial_wheel

    return generate_combinatorial_wheel(pool, pick, guarantee, max_variants, scores)


def _sorted_pool(pool, scores) -> list[int]:
    if scores:
        return sorted(list(pool), key=lambda x: scores.get(x, 0), reverse=True)
    return sorted(list(pool))

def _coverage_pct(wheel: list[list[int]], pool: list[int], guarantee: int) -> float:
    if not wheel:
        return 0.0
    targets = set(itertools.combinations(sorted(pool), guarantee))
    if not targets:
        return 100.0
    covered = set()
    for t in wheel:
        for sub in itertools.combinations(sorted(t), guarantee):
            covered.add(sub)
    return round(len(covered & targets) / len(targets) * 100.0, 2)


def lotto_coverage_pct(
    wheel: list[list[int]], pool: list[int], guarantee: int, condition: int
) -> float:
    """Acoperirea unui lotto design „guarantee dacă condition".

    Țintele sunt submulțimile de `condition` numere din pool; o țintă e acoperită
    dacă un bilet are cel puțin `guarantee` numere comune cu ea. Pentru
    `condition == guarantee` coincide exact cu `_coverage_pct`.
    """
    g, c = int(guarantee), int(condition)
    if not wheel:
        return 0.0
    if c == g:
        return _coverage_pct(wheel, pool, g)
    if c < g:
        raise ValueError(f"condition={c} < guarantee={g}")
    pool_sorted = sorted(int(x) for x in pool)
    targets = list(itertools.combinations(pool_sorted, c))
    if not targets:
        return 100.0
    tickets = [set(int(x) for x in t) for t in wheel]
    covered = 0
    for tg in targets:
        tg_set = set(tg)
        if any(len(tk & tg_set) >= g for tk in tickets):
            covered += 1
    return round(covered / len(targets) * 100.0, 2)


def compute_coverage_pct(
    wheel: list[list[int]],
    pool: list[int],
    guarantee: int,
    condition: int | None = None,
) -> float:
    """API publică pt recalcularea acoperirii garanției pe un set de bilete DAT.

    Folosit din loto_engine.py pentru a revalida `coverage_pct` DUPĂ filtre
    post-wheeling (ex. anomaly filter) care pot elimina bilete fără să
    actualizeze procentul de acoperire raportat inițial de wheeling.
    `condition` (> guarantee) comută pe semantica lotto design „t dacă p"."""
    if condition is not None and int(condition) != int(guarantee):
        return lotto_coverage_pct(wheel, pool, guarantee, condition)
    return _coverage_pct(wheel, pool, guarantee)


def ensure_pool_numbers_on_tickets(
    wheel: list[list[int]],
    pool: list[int],
    pick: int,
) -> list[list[int]]:
    """După un cap de bilete, fiecare număr din pool pe ≥1 bilet dacă încape.

    Trunchierea lexicografică pe pool-ul sortat după scor lăsa numerele slabe
    pe dinafară — pool-ul VALIDAT nu mai era pe bilete. Punem un număr lipsă
    în locul unui număr care apare deja pe alt bilet (duplicat). Scanăm de la
    COADĂ, ca T1 (cel mai bine punctat) să rămână intact — nu doar la cap=1.
    Nu înlocuim un număr unic: un cap de 1 bilet n-are duplicate, deci cele
    `pick` numere tari rămân. Dacă toate numerele de pe bilete sunt unice,
    capacitatea e epuizată (`len(wheel)*pick < len(pool)`) și ne oprim.
    """
    if not wheel or not pool:
        return [list(t) for t in wheel] if wheel else wheel
    pick = int(pick)
    if pick < 1:
        return [list(t) for t in wheel]
    out = [sorted(int(x) for x in t) for t in wheel]
    pool_list = [int(n) for n in pool]

    def _counts() -> Counter:
        c: Counter = Counter()
        for t in out:
            for n in t:
                c[int(n)] += 1
        return c

    counts = _counts()
    missing = [n for n in pool_list if counts[n] == 0]
    for miss in missing:
        placed = False
        # Coada întâi: T1 e cel mai bine punctat; îl atingem doar dacă
        # biletele slabe n-au niciun duplicat de evacuat.
        for ti in range(len(out) - 1, -1, -1):
            t = out[ti]
            for j, n in enumerate(t):
                if counts[int(n)] >= 2:
                    new_t = list(t)
                    new_t[j] = miss
                    out[ti] = sorted(int(x) for x in new_t)
                    counts[int(n)] -= 1
                    counts[miss] += 1
                    placed = True
                    break
            if placed:
                break
    return out


def filter_preserving_coverage(
    wheel: list[list[int]],
    pool: list[int],
    guarantee: int,
    removal_priority: list[int],
) -> tuple[list[list[int]], int]:
    """Elimină bilete din `wheel`, în ordinea din `removal_priority` (indici în
    `wheel`, de la cel mai indezirabil la cel mai puțin dorit — ex. cele mai
    "anomale" statistic), PĂSTRÂND garanția combinatorică — un bilet e eliminat
    DOAR dacă toate țintele lui (subseturi de `guarantee` numere din pool) mai
    sunt acoperite de cel puțin un alt bilet rămas.

    Folosit ca să reconciliem filtrul anti-anomalie (bazat pe scoruri) cu
    garanția de wheeling (bazată pe covering design) — anterior, filtrul putea
    elimina bilete care erau UNICUL acoperitor al unei ținte, spărgând garanția
    promisă utilizatorului chiar și în modul "nelimitat" (max_variants=0).

    Returnează (wheel_filtrat, n_bilete_eliminate).
    """
    wheel = [list(t) for t in wheel]
    targets_per_ticket = [
        set(itertools.combinations(sorted(t), guarantee)) for t in wheel
    ]
    coverage_count: dict[tuple, int] = {}
    for targets in targets_per_ticket:
        for t in targets:
            coverage_count[t] = coverage_count.get(t, 0) + 1

    keep = [True] * len(wheel)
    removed = 0
    for idx in removal_priority:
        if idx < 0 or idx >= len(wheel) or not keep[idx]:
            continue
        targets = targets_per_ticket[idx]
        # Sigur de eliminat DOAR dacă fiecare țintă a lui mai are ≥1 acoperitor
        # rămas (coverage_count>1 acum, înainte de a-l scădea pe al lui). Ținte
        # deja neacoperite (count=0, ex. dintr-un max_variants anterior) NU
        # forțează eliminarea — sunt tratate conservator, biletul e păstrat.
        if all(coverage_count.get(t, 0) > 1 for t in targets):
            keep[idx] = False
            for t in targets:
                coverage_count[t] -= 1
            removed += 1

    result = [w for w, k in zip(wheel, keep) if k]
    return result, removed

def _order_by_scores(wheel: list[list[int]], scores) -> list[list[int]]:
    if not scores:
        return [sorted(t) for t in wheel]
    return sorted(
        [sorted(t) for t in wheel],
        key=lambda t: sum(scores.get(n, 0) for n in t),
        reverse=True,
    )

