"""Regression evidence for bounded geometry candidates added after hitcover v8."""
from itertools import combinations
import random

import pytest

import budget_cover
from covering.probability import wheel_hit_probabilities, wheel_hit_profile


def _old_hitcover(monkeypatch, pool, pick, guarantee, cap, scores):
    with monkeypatch.context() as patch:
        patch.setattr(budget_cover, "balanced_cover_positions", lambda *args: ())
        return budget_cover.wheel_hitcover(pool, pick, guarantee, cap, scores)


@pytest.mark.parametrize("v", [11, 16])
@pytest.mark.parametrize("pick", [5, 6])
@pytest.mark.parametrize("guarantee", [3, 4])
@pytest.mark.parametrize("cap", [3, 7, 10, 30])
def test_new_candidates_never_reduce_any_hit_event(
    monkeypatch, v, pick, guarantee, cap
):
    pool = list(range(1, v + 1))
    scores = {n: float((n * 17) % 23) for n in pool}
    baseline, old_cov = _old_hitcover(
        monkeypatch, pool, pick, guarantee, cap, scores
    )
    candidate, cov = budget_cover.wheel_hitcover(
        pool, pick, guarantee, cap, scores
    )
    assert len(candidate) == len(baseline) <= cap
    assert len(set(map(tuple, candidate))) == len(candidate)
    assert all(len(set(ticket)) == pick for ticket in candidate)
    assert cov >= old_cov
    old_profile = wheel_hit_profile(pool, baseline)
    new_profile = wheel_hit_profile(pool, candidate)
    assert all(
        new >= old
        for new_row, old_row in zip(new_profile, old_profile)
        for new, old in zip(new_row, old_row)
    )
    assert candidate == budget_cover.wheel_hitcover(
        pool, pick, guarantee, cap, scores
    )[0]


@pytest.mark.parametrize(
    "pick,cap,max_num,old3,new3,old4,new4",
    [
        (
            6, 7, 49,
            0.10924206954668168, 0.11314472387222486,
            0.006857141140873135, 0.006871800944749273,
        ),
        (
            5, 10, 40,
            0.11182113287376445, 0.15070498491551124,
            0.007150672940146624, 0.00780198938093675,
        ),
    ],
)
def test_improvements_over_previous_hitcover_at_same_budget(
    monkeypatch, pick, cap, max_num, old3, new3, old4, new4
):
    pool = list(range(1, 17))
    scores = {n: float((n * 17) % 23) for n in pool}
    baseline, _ = _old_hitcover(monkeypatch, pool, pick, 4, cap, scores)
    candidate, _ = budget_cover.wheel_hitcover(pool, pick, 4, cap, scores)
    old_probs = wheel_hit_probabilities(pool, baseline, 6, max_num)["ticket"]
    new_probs = wheel_hit_probabilities(pool, candidate, 6, max_num)["ticket"]
    assert old_probs[3] == pytest.approx(old3)
    assert new_probs[3] == pytest.approx(new3)
    assert old_probs[4] == pytest.approx(old4)
    assert new_probs[4] == pytest.approx(new4)
    assert new_probs[3] > old_probs[3]
    assert new_probs[4] > old_probs[4]


def _brute_profile(pool, tickets):
    """Independent set enumeration, without the production subset DP."""
    ticket_sets = [set(ticket) for ticket in tickets]
    result = []
    for h in range(len(pool) + 1):
        counts = [0] * (len(pool) + 1)
        for subset in combinations(pool, h):
            hits = max(len(set(subset) & ticket) for ticket in ticket_sets)
            for target in range(hits + 1):
                counts[target] += 1
        result.append(tuple(counts))
    return tuple(result)


def test_dominance_certificate_matches_independent_subset_enumeration(monkeypatch):
    pool = list(range(1, 12))
    scores = {n: float((n * 17) % 23) for n in pool}
    baseline, _ = _old_hitcover(monkeypatch, pool, 6, 4, 7, scores)
    candidate, _ = budget_cover.wheel_hitcover(pool, 6, 4, 7, scores)
    assert candidate != baseline
    old_counts = _brute_profile(pool, baseline)
    new_counts = _brute_profile(pool, candidate)
    assert old_counts == wheel_hit_profile(pool, baseline)
    assert new_counts == wheel_hit_profile(pool, candidate)
    assert all(
        new >= old
        for new_row, old_row in zip(new_counts, old_counts)
        for new, old in zip(new_row, old_row)
    )


def test_geometric_order_does_not_change_global_random_state():
    before = random.getstate()
    budget_cover.balanced_cover_positions(15, 6, 9, 2)
    assert random.getstate() == before


def test_new_geometry_is_memoized(monkeypatch):
    budget_cover.balanced_cover_positions.cache_clear()
    first = budget_cover.balanced_cover_positions(11, 6, 7, 0)
    hits_before = budget_cover.balanced_cover_positions.cache_info().hits

    def unexpected_geometry(*args):
        raise AssertionError("Memoized geometry was recomputed")

    monkeypatch.setattr(budget_cover, "candidate_geometry", unexpected_geometry)
    assert budget_cover.balanced_cover_positions(11, 6, 7, 0) is first
    assert budget_cover.balanced_cover_positions.cache_info().hits == hits_before + 1


@pytest.mark.parametrize(
    "v,pick,guarantee,cap", [(6, 6, 4, 7), (11, 6, 3, 30), (16, 6, 6, 7)]
)
def test_complete_and_full_pick_fallbacks_do_not_use_new_candidates(
    monkeypatch, v, pick, guarantee, cap
):
    def unexpected_candidate(*args):
        raise AssertionError("Existing fallback must bypass new candidate search")

    monkeypatch.setattr(budget_cover, "balanced_cover_positions", unexpected_candidate)
    pool = list(range(1, v + 1))
    scores = {n: float((n * 17) % 23) for n in pool}
    budget_cover.wheel_hitcover(pool, pick, guarantee, cap, scores)


@pytest.mark.parametrize("duplicate", [False, True])
def test_pool_repair_cannot_bypass_dominance_gate(monkeypatch, duplicate):
    """A candidate can gain coverage before repair and lose it afterwards."""
    from covering import common

    pool = list(range(1, 17))
    scores = {n: float((n * 17) % 23) for n in pool}
    baseline, _ = _old_hitcover(monkeypatch, pool, 6, 4, 7, scores)
    actual_repair = common.ensure_pool_numbers_on_tickets
    seen = []

    def damaged_repair(candidate, supplied_pool, supplied_pick):
        repaired = actual_repair(candidate, supplied_pool, supplied_pick)
        if repaired and len(repaired) == 7:
            seen.append(repaired)
            if duplicate:
                return [list(range(1, 7)) for _ in repaired]
            # Distinct six-number tickets cover the whole pool but overlap
            # heavily, so the complete hit profile must reject them.
            return [
                [1, 2, 3, 4, 5, 6],
                [1, 2, 3, 7, 8, 9],
                [1, 2, 3, 10, 11, 12],
                [1, 2, 3, 13, 14, 15],
                [1, 2, 3, 4, 5, 16],
                [1, 2, 3, 4, 6, 7],
                [1, 2, 3, 8, 9, 10],
            ]
        return repaired

    # Old geometric candidates also pass through the repair gate. The initial
    # greedy wheel uses its separately imported repair and stays unaffected.
    monkeypatch.setattr(common, "ensure_pool_numbers_on_tickets", damaged_repair)
    result, _ = budget_cover.wheel_hitcover(pool, 6, 4, 7, scores)
    from covering.greedy import generate_combinatorial_wheel
    greedy, _ = generate_combinatorial_wheel(pool, 6, 4, 7, scores)
    assert seen
    assert result == greedy
    assert baseline != greedy
