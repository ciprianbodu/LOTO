"""Exact-dominance profile swaps: same tickets count, guarantees kept, deterministic."""

from itertools import combinations

import pytest

import covering.dispatch as dispatch
from covering.common import _coverage_pct, _sorted_pool, lotto_coverage_pct
from covering.probability import wheel_hit_probabilities, wheel_hit_profile
from covering.profile_swap import improve_hit_profile
from loto_enterprise.core.full_ticket import _fill_variants


def _dominates(new, old):
    return all(a >= b for ra, rb in zip(new, old) for a, b in zip(ra, rb))


def _identity(pool, tickets, *a, **k):
    return [sorted(t) for t in tickets], {"applied": False}


def _pair(monkeypatch, *args, **kwargs):
    monkeypatch.setattr(dispatch, "improve_hit_profile", _identity)
    before, _ = dispatch.generate_wheel(*args, **kwargs)
    monkeypatch.undo()
    after, cov = dispatch.generate_wheel(*args, **kwargs)
    return before, after, cov


@pytest.mark.parametrize(
    "pool_size,pick,g,cond,draw_n",
    [(14, 6, 3, 4, 6), (11, 6, 4, 5, 6), (12, 5, 3, 4, 6), (10, 5, 3, 4, 5)],
)
def test_lotto_design_keeps_t_if_p_and_dominates(monkeypatch, pool_size, pick, g, cond, draw_n):
    pool = list(range(1, pool_size + 1))
    before, after, cov = _pair(
        monkeypatch, "lotto", pool, pick, g, 0, None, condition=cond, draw_n=draw_n
    )
    assert len({tuple(t) for t in after}) == len(before)
    assert _dominates(wheel_hit_profile(pool, after), wheel_hit_profile(pool, before))
    # Exhaustive t-if-p check.
    sets = [set(t) for t in after]
    assert all(
        any(len(s & set(c)) >= g for s in sets) for c in combinations(pool, cond)
    )
    assert cov == lotto_coverage_pct(after, pool, g, cond) == 100.0


def test_lotto_measured_gain_is_strict(monkeypatch):
    pool = list(range(1, 15))
    before, after, _ = _pair(
        monkeypatch, "lotto", pool, 6, 3, 0, None, condition=4, draw_n=6
    )
    p0 = wheel_hit_probabilities(pool, before, 6, 49)["ticket"]
    p1 = wheel_hit_probabilities(pool, after, 6, 49)["ticket"]
    assert p1[3] > p0[3] and p1[4] >= p0[4]


@pytest.mark.parametrize(
    "pool_size,pick,g,budget,draw_n",
    [(16, 6, 3, 0, 6), (14, 5, 3, 0, 6), (14, 6, 3, 20, 6), (11, 6, 3, 7, 6), (13, 5, 3, 100, 5)],
)
def test_classic_and_budget_preserve_count_coverage(monkeypatch, pool_size, pick, g, budget, draw_n):
    pool = list(range(1, pool_size + 1))
    method = dispatch.resolve_wheel_method(budget, "")
    before, after, cov = _pair(
        monkeypatch, method, pool, pick, g, budget, None, draw_n=draw_n
    )
    assert len(after) == len(before) == len({tuple(t) for t in after})
    if budget:
        assert len(after) <= budget
    assert _dominates(wheel_hit_profile(pool, after), wheel_hit_profile(pool, before))
    assert cov == _coverage_pct(after, pool, g) >= _coverage_pct(before, pool, g)
    assert set(pool) == {n for t in after for n in t}


def test_explicit_greedy_is_untouched(monkeypatch):
    pool = list(range(1, 12))
    before, after, _ = _pair(monkeypatch, "greedy", pool, 6, 3, 7, None, draw_n=6)
    assert before == after


def test_deterministic_and_frozen_prefix():
    pool = list(range(1, 13))
    wheel, _ = dispatch.generate_wheel("greedy", pool, 6, 3, 9, None)
    a, audit_a = improve_hit_profile(pool, wheel, 6, frozen=4)
    b, audit_b = improve_hit_profile(pool, wheel, 6, frozen=4)
    assert a == b and audit_a == audit_b
    assert a[:4] == [sorted(t) for t in wheel[:4]]
    assert _dominates(wheel_hit_profile(pool, a), wheel_hit_profile(pool, wheel))


def test_out_of_bounds_returns_input():
    pool = list(range(1, 18))
    wheel = [[1, 2, 3, 4, 5, 6]]
    out, audit = improve_hit_profile(pool, wheel, 6)
    assert out == wheel and not audit["applied"]


def test_full_ticket_fill_keeps_base_and_dominates(monkeypatch):
    import covering.profile_swap as ps

    pool = _sorted_pool(list(range(1, 10)), None)
    real = ps.improve_hit_profile
    monkeypatch.setattr(ps, "improve_hit_profile", _identity)
    before, n0 = _fill_variants(pool, 6, 3, 30, None)
    monkeypatch.setattr(ps, "improve_hit_profile", real)
    after, n1 = _fill_variants(pool, 6, 3, 30, None)
    assert n0 == n1 and after[:n1] == before[:n0]
    assert len({tuple(t) for t in after}) == len(before)
    assert _dominates(wheel_hit_profile(pool, after), wheel_hit_profile(pool, before))
