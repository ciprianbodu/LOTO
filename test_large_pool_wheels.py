"""Full-universe wheels: exact guarantees, bounded memory and legacy isolation."""

from __future__ import annotations

import itertools
import math
import random

import pytest

from covering import large_pool as large
from covering.common import compute_coverage_pct
from covering.dispatch import generate_wheel


def _classical_covered(wheel, guarantee):
    return {sub for ticket in wheel for sub in itertools.combinations(sorted(ticket), guarantee)}


@pytest.mark.parametrize("v,pick", [(49, 6), (40, 5), (45, 5)])
@pytest.mark.parametrize("cap", [1, 9, 112, 10_000])
def test_full_game_pools_respect_budget_and_report_exact_classical_coverage(v, pick, cap):
    pool = list(range(1, v + 1))
    scores = {n: -float(n) for n in pool}
    wheel, coverage = generate_wheel("hitcover", pool, pick, 4, cap, scores, max_num=v)
    assert 0 < len(wheel) <= cap
    assert len({tuple(t) for t in wheel}) == len(wheel)
    assert all(len(t) == len(set(t)) == pick and set(t) <= set(pool) for t in wheel)
    represented = set().union(*(set(t) for t in wheel))
    if cap * pick >= v:
        assert represented == set(pool)
    else:
        assert len(represented) == cap * pick
    exact = len(_classical_covered(wheel, 4)) / math.comb(v, 4) * 100
    expected = min(99.99, round(exact, 2)) if exact < 100 else 100.0
    assert coverage == expected
    assert compute_coverage_pct(wheel, pool, 4) == coverage


@pytest.mark.parametrize("v,pick", [(49, 6), (40, 5), (45, 5)])
def test_unlimited_default_guarantee_covers_every_target_at_full_game_maximum(v, pick):
    pool = list(range(1, v + 1))
    wheel, coverage = generate_wheel("lajolla", pool, pick, 4)
    covered = _classical_covered(wheel, 4)
    assert covered == set(itertools.combinations(pool, 4))
    assert coverage == 100.0
    assert len(wheel) == len({tuple(t) for t in wheel})
    assert set().union(*(set(t) for t in wheel)) == set(pool)


@pytest.mark.parametrize("cap", [1, 8, 9, 112])
def test_budgeted_full_system_never_materializes_the_full_universe(cap):
    wheel, coverage = large.generate_large_pool_wheel(range(1, 50), 6, 6, cap)
    assert len(wheel) == len({tuple(t) for t in wheel}) == cap
    assert coverage == round(cap / math.comb(49, 6) * 100, 2)
    assert len(set().union(*(set(t) for t in wheel))) == min(cap * 6, 49)


@pytest.mark.parametrize("pick,guarantee", [(6, 6), (6, 5), (5, 5)])
def test_huge_unlimited_request_rejected_before_constructing_tickets(monkeypatch, pick, guarantee):
    def forbidden(*args, **kwargs):
        pytest.fail("construction started before the unlimited-system guard")

    monkeypatch.setattr(large, "_canonical_cover", forbidden)
    with pytest.raises(ValueError, match="plafon pozitiv"):
        large.generate_large_pool_wheel(range(1, 50), pick, guarantee)


def test_default_full_universe_request_is_permitted_by_prevalidation():
    for v, pick in [(49, 6), (40, 5), (45, 5)]:
        large.validate_large_pool_request(v, pick, 4)
        large.validate_large_pool_request(v, pick, pick, 112)


@pytest.mark.parametrize("pick,guarantee", [(6, 6), (6, 4), (5, 5)])
def test_excessive_direct_api_budget_is_explicitly_rejected(monkeypatch, pick, guarantee):
    def forbidden(*args, **kwargs):
        pytest.fail("an excessive direct budget reached construction")

    monkeypatch.setattr(large, "_canonical_cover", forbidden)
    with pytest.raises(ValueError, match="cel mult 100,000"):
        large.generate_large_pool_wheel(range(1, 50), pick, guarantee, 13_983_816)


@pytest.mark.parametrize("guarantee,condition", [(2, 3), (3, 5), (4, 6), (5, 6)])
def test_conditional_coverage_agrees_with_independent_target_enumeration(guarantee, condition):
    pool = list(range(1, 22))
    wheel, coverage = generate_wheel("lajolla", pool, 6, guarantee, 7, condition=condition)
    tickets = [set(ticket) for ticket in wheel]
    count = sum(any(len(set(target) & ticket) >= guarantee for ticket in tickets)
                for target in itertools.combinations(pool, condition))
    assert coverage == round(count / math.comb(21, condition) * 100, 2)
    assert compute_coverage_pct(wheel, pool, guarantee, condition) == coverage


def test_conditional_full_universe_one_ticket_matches_closed_form():
    wheel, coverage = large.generate_large_pool_wheel(range(1, 50), 6, 4, 1, condition=6)
    count = sum(math.comb(6, h) * math.comb(43, 6 - h) for h in range(4, 7))
    assert coverage == round(count / math.comb(49, 6) * 100, 2)
    assert len(wheel) == 1


def test_conditional_complete_cover_is_proved_without_scanning_all_six_subsets(monkeypatch):
    import numpy as np

    wheel, coverage = large.generate_large_pool_wheel(range(1, 41), 5, 4)
    assert coverage == 100
    def forbidden(*args, **kwargs):
        pytest.fail("a complete classical cover already proves the conditional guarantee")

    monkeypatch.setattr(np, "zeros", forbidden)
    assert large.large_pool_coverage_pct(wheel, list(range(1, 41)), 4, 6) == 100


def test_scored_position_design_is_deterministic_under_relabelling_and_does_not_touch_rng():
    pool = list(range(1, 50))
    scores = {n: -float(n) for n in pool}
    random.seed(418)
    state = random.getstate()
    wheel, coverage = large.generate_large_pool_wheel(pool, 6, 4, 112, scores)
    other, other_cov = large.generate_large_pool_wheel(
        [n + 100 for n in reversed(pool)], 6, 4, 112, {n + 100: s for n, s in scores.items()}
    )
    assert [[n - 100 for n in ticket] for ticket in other] == wheel
    assert other_cov == coverage
    assert random.getstate() == state


def test_large_dispatch_avoids_every_exponential_profile_optimizer(monkeypatch):
    import covering.dispatch as dispatch
    import covering.probability as probability

    def forbidden(*args, **kwargs):
        pytest.fail("large pool reached an optimizer limited to an exponential subset table")

    monkeypatch.setattr(probability, "wheel_hit_profile", forbidden)
    monkeypatch.setattr(probability, "wheel_hit_probabilities", forbidden)
    monkeypatch.setattr(dispatch, "improve_hit_profile", forbidden)
    monkeypatch.setattr(dispatch, "wheel_hitcover", forbidden)
    wheel, coverage = dispatch.generate_wheel("hitcover", list(range(1, 50)), 6, 4, 112)
    assert len(wheel) == 112 and 0 < coverage < 100


def test_legacy_pool_does_not_enter_large_route(monkeypatch):
    import covering.dispatch as dispatch

    def forbidden(*args, **kwargs):
        pytest.fail("an existing pool entered the new route")

    monkeypatch.setattr(dispatch, "generate_large_pool_wheel", forbidden)
    wheel, coverage = dispatch.generate_wheel("greedy", list(range(1, 20)), 5, 4, 1)
    assert len(wheel) == 1 and coverage < 100


def test_direct_facades_use_the_same_safe_large_route():
    from covering.designs import wheel_lajolla, wheel_lotto
    from covering.greedy import generate_combinatorial_wheel

    pool = list(range(1, 50))
    expected = large.generate_large_pool_wheel(pool, 6, 4, 112)
    assert generate_combinatorial_wheel(pool, 6, 4, 112) == expected
    assert wheel_lajolla(pool, 6, 4, 112) == expected
    assert wheel_lotto(pool, 6, 4, 4, 112) == expected


def test_large_pool_rejects_duplicate_numbers_and_impossible_condition():
    with pytest.raises(ValueError, match="distincte"):
        large.generate_large_pool_wheel([*range(1, 49), 48], 6, 4, 112)
    with pytest.raises(ValueError, match="condition"):
        large.generate_large_pool_wheel(range(1, 50), 6, 4, 112, condition=3)
