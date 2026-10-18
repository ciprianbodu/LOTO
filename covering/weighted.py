"""Experimental score-weighted covering designs.

Relabeling a complete design by position degree preserves its coverage and
uniform-draw hit probabilities. It allocates more appearances to higher
scores; scorer values are not calibrated draw probabilities. The capped
greedy objective instead prioritizes score-weighted target coverage, which
can reduce ordinary coverage or hit probability at equal cost. Neither
operation establishes a predictive advantage; this is explicit opt-in.
"""

from __future__ import annotations

import itertools
import logging
import math
from collections import Counter

from covering.common import (
    _coverage_pct,
    _coverage_ratio_pct,
    _sorted_pool,
    ensure_pool_numbers_on_tickets,
)
from covering.designs import _load_lajolla
from covering.probability import _integer

logger = logging.getLogger(__name__)


def align_positions_by_degree(
    blocks: list[list[int]], v: int
) -> tuple[list[list[int]], list[int]]:
    """Rearanjează pozițiile 1..v ale unui design astfel încât poziția 1 să aibă
    frecvența maximă, poziția 2 următoarea etc.

    Păstrează neschimbate garanția și proprietatea de acoperire a designului
    (izomorfism prin permutarea simbolurilor).
    """
    counts = Counter(x for b in blocks for x in b)
    # Sortează pozițiile 1..v descrescător după frecvență, cu tie-break stabil pe index
    pos_order = sorted(range(1, v + 1), key=lambda p: (-counts[p], p))

    # Permutare: poziția pos_order[0] devine 1, pos_order[1] devine 2 etc.
    perm = {orig_pos: rank + 1 for rank, orig_pos in enumerate(pos_order)}
    aligned_blocks = [sorted(perm[x] for x in b) for b in blocks]
    return aligned_blocks, pos_order


def _calculate_weights(pool: list[int], scores: dict[int, float] | None) -> dict[int, float]:
    """Finite weights in [1, 3]; missing/invalid scores receive the minimum.

    Scale before subtraction so opposite finite scores near float limits do
    not overflow, collapsing all middle weights to the wrong endpoint.
    """
    values = {}
    for n in pool:
        try:
            value = float(scores[n]) if scores and n in scores else float("nan")
        except (TypeError, ValueError, OverflowError):
            continue
        if math.isfinite(value):
            values[n] = value
    if not values or min(values.values()) == max(values.values()):
        return {n: 1.0 for n in pool}
    minimum, maximum = min(values.values()), max(values.values())
    scale = max(abs(minimum), abs(maximum))
    lower, upper = minimum / scale, maximum / scale
    return {
        n: 1.0 + 2.0 * max(0.0, min(1.0, (values.get(n, minimum) / scale - lower) / (upper - lower)))
        for n in pool
    }


def _validated(pool, pick, guarantee, max_variants, scores):
    pick, guarantee, budget = map(_integer, (pick, guarantee, max_variants))
    if not 1 <= guarantee <= pick:
        raise ValueError("Require 1 <= guarantee <= pick")
    ordered = _sorted_pool([_integer(n) for n in pool], scores)
    if len(set(ordered)) != len(ordered):
        raise ValueError("Pool numbers must be distinct")
    return ordered, pick, guarantee, budget


def weighted_combinatorial_wheel(
    pool, pick=6, guarantee=4, max_variants=0, scores=None
) -> tuple[list[list[int]], float]:
    """Greedy Set Cover Ponderat.

    Prioritizează acoperirea țintelor formate din numere cu scor mare.
    """
    pool, pick, guarantee, max_variants = _validated(pool, pick, guarantee, max_variants, scores)
    pool_len = len(pool)
    if pool_len < pick:
        return [], 0.0

    # Cazul guarantee == pick (sistem complet sau trunchiat)
    if int(guarantee) == int(pick):
        n_full = math.comb(pool_len, pick)
        combinations = itertools.combinations(pool, pick)
        if max_variants > 0 and n_full > max_variants:
            combinations = itertools.islice(combinations, max_variants)
        wheel = [sorted(c) for c in combinations]
        if max_variants > 0:
            wheel = ensure_pool_numbers_on_tickets(wheel, pool, pick)
        return wheel, _coverage_pct(wheel, pool, guarantee)

    weights = _calculate_weights(pool, scores)
    all_targets = [
        tuple(sorted(t)) for t in itertools.combinations(pool, guarantee)
    ]
    total_targets = len(all_targets)

    # Sortăm țintele descrescător după suma ponderilor numerelor componente
    all_targets.sort(key=lambda t: sum(weights.get(n, 1.0) for n in t), reverse=True)
    target_weights = [sum(weights.get(n, 1.0) for n in t) for t in all_targets]
    target_index = {t: i for i, t in enumerate(all_targets)}

    covered_mask = 0
    covered_count = 0
    wheel = []
    target_cursor = 0
    max_search_per_iter = 10000 if pool_len <= 15 else 50000

    _tm_cache: dict[tuple[int, ...], int] = {}

    def _ticket_mask(ticket: tuple[int, ...]) -> int:
        cached = _tm_cache.get(ticket)
        if cached is not None:
            return cached
        m = 0
        for sub in itertools.combinations(ticket, guarantee):
            idx = target_index.get(sub)
            if idx is not None:
                m |= 1 << idx
        _tm_cache[ticket] = m
        return m

    while covered_count < total_targets:
        if max_variants > 0 and len(wheel) >= max_variants:
            break

        while target_cursor < total_targets and (covered_mask >> target_cursor) & 1:
            target_cursor += 1

        if target_cursor >= total_targets:
            break

        target_to_cover = all_targets[target_cursor]
        base_ticket = set(target_to_cover)
        remaining_pool = [n for n in pool if n not in base_ticket]

        best_ticket = None
        best_gain = -1.0
        best_mask = 0
        search_count = 0

        needed_extra = pick - guarantee
        if len(remaining_pool) >= needed_extra:
            for extra_nums in itertools.combinations(remaining_pool, needed_extra):
                ticket = tuple(sorted(list(base_ticket) + list(extra_nums)))
                tmask = _ticket_mask(ticket)
                uncovered = tmask & ~covered_mask
                if not uncovered:
                    continue

                # Câștig ponderat: suma ponderilor țintelor nou acoperite
                gain = 0.0
                temp_mask = uncovered
                while temp_mask:
                    lsb = temp_mask & -temp_mask
                    idx = (lsb.bit_length() - 1)
                    gain += target_weights[idx]
                    temp_mask ^= lsb

                if gain > best_gain:
                    best_gain = gain
                    best_ticket = ticket
                    best_mask = tmask

                search_count += 1
                if search_count > max_search_per_iter:
                    break

        if best_ticket:
            wheel.append(list(best_ticket))
            covered_mask |= best_mask
            covered_count = covered_mask.bit_count()
        else:
            break

    if max_variants > 0:
        wheel = ensure_pool_numbers_on_tickets(wheel, pool, pick)
        # Recalculăm acoperirea unică după asigurarea numerelor din pool
        cov_mask = 0
        for t in wheel:
            ticket = tuple(sorted(int(x) for x in t))
            for sub in itertools.combinations(ticket, guarantee):
                idx = target_index.get(sub)
                if idx is not None:
                    cov_mask |= 1 << idx
        coverage_pct = _coverage_ratio_pct(cov_mask.bit_count(), total_targets)
    else:
        coverage_pct = _coverage_ratio_pct(covered_count, total_targets)

    return wheel, coverage_pct


def wheel_weighted(
    pool, pick, guarantee, max_variants=0, scores=None
) -> tuple[list[list[int]], float]:
    """Punctul de intrare pentru Weighted Wheeling.

    1. Dacă nu există plafon de variante (max_variants == 0), folosește designul
       complet validat La Jolla C(v, pick, guarantee), aliniat prin izomorfism
       pe gradele de apariție (`align_positions_by_degree`). Astfel, numerele de top
       primesc pozițiile cu cele mai multe apariții, garantând 100.0% acoperire.
    2. Dacă există un buget fix (max_variants > 0) sau nu există fișier de design,
       folosește `weighted_combinatorial_wheel`, concentrând acoperirea pe
       combinațiile numerelor cu scor maxim.
    """
    sorted_p, pick, guarantee, max_variants = _validated(pool, pick, guarantee, max_variants, scores)
    v = len(sorted_p)
    if v < pick:
        return [], 0.0

    # Când nu există limită de variante, încercăm designul canonic La Jolla aliniat
    if max_variants <= 0:
        design = _load_lajolla(v, pick, guarantee)
        if design is not None:
            aligned_design, _ = align_positions_by_degree(design, v)
            wheel = []
            for blk in aligned_design:
                mapped = [sorted_p[i - 1] for i in blk if 1 <= i <= v]
                if len(mapped) == pick and len(set(mapped)) == pick:
                    wheel.append(sorted(mapped))
            if wheel:
                cov = _coverage_pct(wheel, sorted_p, guarantee)
                if cov >= 100.0:
                    return wheel, 100.0

    # Altfel rulăm algoritmul greedy ponderat
    return weighted_combinatorial_wheel(
        sorted_p, pick, guarantee, max_variants=max_variants, scores=scores
    )
