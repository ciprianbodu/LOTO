"""Deterministic maximum-coverage heuristic for small ticket budgets.

Geometry only: scores determine the canonical mapping to numbers, never a
probability. Exhaustive candidate scan is not an optimality certificate.
"""

from functools import lru_cache
from itertools import combinations


@lru_cache(maxsize=16)
def candidate_geometry(v, pick, target):
    blocks = tuple(combinations(range(v), pick))
    targets = {t: i for i, t in enumerate(combinations(range(v), target))}
    masks = tuple(
        sum(1 << targets[t] for t in combinations(block, target)) for block in blocks
    )
    return blocks, masks


@lru_cache(maxsize=64)
def maximum_cover_positions(v, pick, target, budget):
    """Greedy marginal gain, then two strict-improvement one-ticket swap passes."""
    blocks, masks = candidate_geometry(v, pick, target)
    selected = []
    covered = 0
    for _ in range(min(budget, len(blocks))):
        best = max(range(len(blocks)), key=lambda j: (masks[j] & ~covered).bit_count())
        if not masks[best] & ~covered:
            break
        selected.append(best)
        covered |= masks[best]
    for _ in range(2):
        improved = False
        for slot in range(len(selected)):
            others = 0
            for i, j in enumerate(selected):
                if i != slot:
                    others |= masks[j]
            incumbent = (masks[selected[slot]] | others).bit_count()
            best = max(
                range(len(blocks)), key=lambda j: (masks[j] & ~others).bit_count()
            )
            if (masks[best] | others).bit_count() > incumbent:
                selected[slot] = best
                improved = True
        if not improved:
            break
    return tuple(blocks[j] for j in selected)


@lru_cache(maxsize=128)
def balanced_cover_positions(v, pick, budget, seed):
    """Four-subset cover with three-subset gains as a geometric tie-break.

    Three fixed local seeds diversify equal-gain choices, independent of pool
    numbers, scores and draw history. A bounded three-pass swap search keeps
    improvements in the same lexicographic objective. This is a candidate
    heuristic only: the caller must certify the complete hit profile after
    mapping and pool repair before using the result.
    """
    from random import Random

    blocks, primary = candidate_geometry(v, pick, 4)
    secondary = candidate_geometry(v, pick, 3)[1]
    order = list(range(len(blocks)))
    Random(seed).shuffle(order)
    priority = [0] * len(order)
    for i, j in enumerate(order):
        priority[j] = len(order) - i

    def best(covered, covered_secondary):
        gains = [(mask & ~covered).bit_count() for mask in primary]
        top = max(gains)
        choices = (j for j, gain in enumerate(gains) if gain == top)
        return max(
            choices,
            key=lambda j: (
                (secondary[j] & ~covered_secondary).bit_count(),
                priority[j],
            ),
        )

    selected = []
    covered = covered_secondary = 0
    for _ in range(min(budget, len(blocks))):
        j = best(covered, covered_secondary)
        if not primary[j] & ~covered:
            break
        selected.append(j)
        covered |= primary[j]
        covered_secondary |= secondary[j]

    for _ in range(3):
        improved = False
        for slot in range(len(selected)):
            others = others_secondary = 0
            for i, j in enumerate(selected):
                if i != slot:
                    others |= primary[j]
                    others_secondary |= secondary[j]
            old = selected[slot]
            j = best(others, others_secondary)
            incumbent = (
                (primary[old] | others).bit_count(),
                (secondary[old] | others_secondary).bit_count(),
            )
            candidate = (
                (primary[j] | others).bit_count(),
                (secondary[j] | others_secondary).bit_count(),
            )
            if candidate > incumbent:
                selected[slot] = j
                improved = True
        if not improved:
            break
    return tuple(blocks[j] for j in selected)


def wheel_maxcover(pool, pick, guarantee, max_variants=0, scores=None):
    """Keep the incumbent unless exact coverage improves after pool repair.

    Bounded to the UI geometry and <=64 tickets. Other cases preserve the
    canonical greedy path. Does not change production defaults.
    """
    from covering.greedy import generate_combinatorial_wheel
    from loto_enterprise.core.ranking import rank_by_score
    from wheeling_methods import ensure_pool_numbers_on_tickets

    pool = list(pool)
    base, base_cov = generate_combinatorial_wheel(
        pool, pick, guarantee, max_variants, scores
    )
    if not (
        pick <= len(pool) <= 16
        and len(set(pool)) == len(pool)
        and 1 <= guarantee < pick <= 6
        and 1 <= max_variants <= 64
        and base_cov < 100
    ):
        return base, base_cov
    ordered = rank_by_score({n: (scores or {}).get(n, 0.0) for n in pool}, len(pool))
    if len(ordered) != len(pool):
        return base, base_cov
    positions = maximum_cover_positions(len(pool), pick, guarantee, len(base))
    candidate = [sorted(ordered[i] for i in block) for block in positions]
    candidate = ensure_pool_numbers_on_tickets(candidate, pool, pick)

    def covered(wheel):
        return {t for block in wheel for t in combinations(sorted(block), guarantee)}

    candidate_count = len(covered(candidate))
    if candidate_count > len(covered(base)):
        from math import comb

        from covering.common import _coverage_ratio_pct

        return candidate, _coverage_ratio_pct(
            candidate_count, comb(len(pool), guarantee)
        )
    return base, base_cov


def wheel_hitcover(pool, pick, guarantee, max_variants=0, scores=None):
    """Budget optimization, accepted only with exact hit dominance.

    Keep the canonical greedy wheel unless a candidate uses the same number
    of tickets and covers at least as many pool intersections at EVERY hit
    threshold and EVERY intersection size. This protects 5/40's six-number
    draws as well as other geometries without inferring odds from scores.
    Classical guarantee coverage cannot fall, even when rounded percentages
    are equal. No optimality or predictive advantage is claimed.
    """
    from covering.common import (
        _coverage_ratio_pct,
        ensure_pool_numbers_on_tickets,
    )
    from covering.greedy import generate_combinatorial_wheel
    from covering.probability import wheel_hit_profile
    from loto_enterprise.core.ranking import rank_by_score
    from math import comb

    pool = list(pool)
    base, base_cov = generate_combinatorial_wheel(
        pool, pick, guarantee, max_variants, scores
    )
    if not (
        pick <= len(pool) <= 16
        and len(set(pool)) == len(pool)
        and 1 <= guarantee < pick <= 6
        and 1 <= max_variants <= 64
        and base_cov < 100.0
    ):
        return base, base_cov
    ordered = rank_by_score({n: (scores or {}).get(n, 0.0) for n in pool}, len(pool))
    if len(ordered) != len(pool):
        return base, base_cov
    incumbent, profile = base, wheel_hit_profile(pool, base)
    candidates = [
        maximum_cover_positions(len(pool), pick, target, len(base))
        for target in dict.fromkeys((guarantee, 3, 4))
        if 1 <= target < pick
    ]
    if pick >= 5:
        candidates.extend(
            balanced_cover_positions(len(pool), pick, len(base), seed)
            for seed in range(3)
        )
    for positions in candidates:
        candidate = [sorted(ordered[i] for i in block) for block in positions]
        candidate = ensure_pool_numbers_on_tickets(candidate, pool, pick)
        if len(candidate) != len(base) or len(set(map(tuple, candidate))) != len(
            candidate
        ):
            continue
        candidate_profile = wheel_hit_profile(pool, candidate)
        if any(
            a < b
            for a_row, b_row in zip(candidate_profile, profile)
            for a, b in zip(a_row, b_row)
        ):
            continue
        # A strict improvement must concern a possible 3+/4+ lottery event.
        if not any(
            candidate_profile[h][t] > profile[h][t]
            for h in range(3, min(6, len(pool)) + 1)
            for t in (3, 4)
            if t <= h
        ):
            continue
        incumbent, profile = candidate, candidate_profile
    return incumbent, _coverage_ratio_pct(
        profile[guarantee][guarantee], comb(len(pool), guarantee)
    )
