"""Teste pentru Multi-Hit Wheeling Optimization."""

from __future__ import annotations

import pytest

from covering.dispatch import generate_wheel, resolve_wheel_method
from covering.multihit import multi_hit_stats, wheel_multihit


def test_multi_hit_stats_basic():
    pool = [1, 2, 3, 4, 5]
    # Două bilete care se suprapun
    wheel = [[1, 2, 3, 4], [1, 2, 3, 5]]
    # C(5, 3) = 10 ținte
    stats = multi_hit_stats(wheel, pool, guarantee=3)

    assert stats["total_targets"] == 10
    # Ținta (1, 2, 3) e acoperită de AMBELE bilete
    assert stats["covered_2plus"] >= 1
    assert stats["covered_1plus"] >= stats["covered_2plus"]
    assert stats["pct_1plus"] > 0.0


def test_wheel_multihit_budget():
    pool = list(range(1, 12))
    pick = 5
    guarantee = 3
    max_variants = 12

    wheel, cov = wheel_multihit(
        pool, pick=pick, guarantee=guarantee, max_variants=max_variants, min_covers=2
    )

    assert len(wheel) <= max_variants
    assert cov > 0.0

    # Fiecare număr din pool apare pe cel puțin un bilet
    seen = set(x for b in wheel for x in b)
    assert seen == set(pool)

    # Verificăm că avem ținte cu acoperire multi-hit (2+)
    stats = multi_hit_stats(wheel, pool, guarantee=guarantee)
    assert stats["covered_2plus"] > 0


def test_wheel_multihit_uncapped_guarantees_double_cover():
    pool = list(range(1, 9))
    pick = 5
    guarantee = 3

    # Fără limită de variante, min_covers=2 trebuie să acopere TOATE țintele de 2 ori
    wheel, cov = wheel_multihit(
        pool, pick=pick, guarantee=guarantee, max_variants=0, min_covers=2
    )

    stats = multi_hit_stats(wheel, pool, guarantee=guarantee)
    assert stats["min_covers"] >= 2
    assert stats["pct_2plus"] == 100.0


def test_dispatch_multihit():
    assert resolve_wheel_method(10, requested="multihit") == "multihit"

    pool = list(range(1, 10))
    wheel, cov = generate_wheel(
        "multihit", pool, pick=5, guarantee=3, max_variants=6
    )
    assert len(wheel) == 6
    assert cov > 0.0


def test_multiple_cover_stats_count_distinct_tickets_only():
    stats = multi_hit_stats([[1, 2, 3], [3, 2, 1]], [1, 2, 3, 4], 2)
    assert stats["total_targets"] == 6
    assert stats["covered_1plus"] == 3
    assert stats["covered_2plus"] == 0


@pytest.mark.parametrize("wheel,pool,g", [([[1, 1, 2]], [1, 2, 3], 2), ([[1, 2, 4]], [1, 2, 3], 2), ([[1, 2]], [1, 1, 2], 1), ([[1, 2.5]], [1, 2, 3], 1), ([[]], [1, 2, 3], 1)])
def test_multiple_cover_stats_reject_invalid_numbers(wheel, pool, g):
    with pytest.raises(ValueError):
        multi_hit_stats(wheel, pool, g)


@pytest.mark.parametrize("pool,pick,g", [(list(range(1, 7)), 6, 4), (list(range(1, 8)), 5, 5)])
def test_multihit_uncapped_rejects_impossible_distinct_double_cover(pool, pick, g):
    with pytest.raises(ValueError, match="impossible"):
        wheel_multihit(pool, pick, g)


def test_multihit_full_guarantee_budget_reports_attainable_primary_coverage():
    pool = list(range(1, 8))
    wheel, coverage = wheel_multihit(pool, 5, 5, max_variants=3)
    assert len({tuple(t) for t in wheel}) == 3
    assert coverage == pytest.approx(100 * 3 / 21, abs=.005)
    assert multi_hit_stats(wheel, pool, 5)["covered_2plus"] == 0


@pytest.mark.parametrize("multiplicity", [1, 2, 3, 6])
def test_multihit_fulfills_feasible_multiplicity(multiplicity):
    pool = list(range(1, 8))
    wheel, coverage = wheel_multihit(pool, 5, 3, min_covers=multiplicity)
    assert coverage == 100.0
    assert len(wheel) == len({tuple(t) for t in wheel})
    # Independent certificate directly counts subset containment.
    import itertools
    assert all(sum(set(target) <= set(ticket) for ticket in wheel) >= multiplicity
               for target in itertools.combinations(pool, 3))


def test_multihit_coverage_describes_tickets_after_pool_repair(monkeypatch):
    import covering.multihit as module
    from covering.common import compute_coverage_pct
    repaired = [[1, 2, 3, 4, 5]]
    monkeypatch.setattr(module, "ensure_pool_numbers_on_tickets", lambda *args: repaired)
    pool = list(range(1, 10))
    wheel, coverage = module.wheel_multihit(pool, 5, 3, 6)
    assert wheel == repaired
    assert coverage == compute_coverage_pct(wheel, pool, 3)


@pytest.mark.parametrize("kwargs", [{"pick": 0}, {"guarantee": 0}, {"guarantee": 7}, {"min_covers": 0}, {"min_covers": 1.5}, {"max_variants": 1.5}])
def test_multihit_rejects_invalid_geometry(kwargs):
    with pytest.raises(ValueError):
        wheel_multihit(list(range(1, 10)), **kwargs)


def test_multihit_rejects_duplicate_pool():
    with pytest.raises(ValueError):
        wheel_multihit([1, 2, 3, 4, 5, 5], 5, 3)
