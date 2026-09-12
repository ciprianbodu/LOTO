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


def wheel_maxcover(pool, pick, guarantee, max_variants=0, scores=None):
    """Keep the incumbent unless exact coverage improves after pool repair.

    Bounded to the UI geometry and <=64 tickets. Other cases preserve the
    canonical greedy path. Does not change production defaults.
    """
    from loto_engine import generate_combinatorial_wheel
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
    # Same actual ticket count, even if the incumbent stopped before the cap.
    positions = maximum_cover_positions(len(pool), pick, guarantee, len(base))
    candidate = [sorted(ordered[i] for i in block) for block in positions]
    candidate = ensure_pool_numbers_on_tickets(candidate, pool, pick)

    def covered(wheel):
        return {t for block in wheel for t in combinations(sorted(block), guarantee)}

    candidate_count = len(covered(candidate))
    if candidate_count > len(covered(base)):
        from math import comb

        return candidate, round(100 * candidate_count / comb(len(pool), guarantee), 2)
    return base, base_cov
