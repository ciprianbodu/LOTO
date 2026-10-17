"""Independent exhaustive coverage checks and no-lookahead regression."""

from itertools import combinations, product
from math import comb

import numpy as np
import pytest

from budget_cover import wheel_maxcover
from loto_engine import generate_combinatorial_wheel
from scripts.analysis import bench_budget_cover as bench


def covered(wheel, target):
    return {t for ticket in wheel for t in combinations(sorted(ticket), target)}


@pytest.mark.parametrize(
    "v,pick,target,budget", list(product((6, 11, 16), (5, 6), (3, 4), (1, 7, 10)))
)
def test_budget_cover_never_loses_coverage(v, pick, target, budget):
    pool = list(range(2, 2 * v + 1, 2))[::-1]
    scores = {n: float(n % 7) for n in pool}
    args = (pool, pick, target, budget, scores)
    base, _ = generate_combinatorial_wheel(*args)
    wheel, coverage = wheel_maxcover(*args)
    assert len(wheel) <= len(base) <= budget
    assert all(len(set(t)) == len(t) == pick and set(t) <= set(pool) for t in wheel)
    assert len(set(map(tuple, wheel))) == len(wheel)
    assert len(covered(wheel, target)) >= len(covered(base, target))
    assert coverage == round(100 * len(covered(wheel, target)) / comb(v, target), 2)
    if len(wheel) * pick >= v:
        assert set().union(*map(set, wheel)) == set(pool)
    assert wheel_maxcover(*args) == (wheel, coverage)


@pytest.mark.parametrize("scores", [None, {}, {n: 1.0 for n in range(1, 12)}])
def test_empty_and_tied_scores(scores):
    args = (list(range(1, 12)), 5, 3, 7, scores)
    a = wheel_maxcover(*args)
    assert a == wheel_maxcover(*args)
    assert len(covered(a[0], 3)) >= len(
        covered(generate_combinatorial_wheel(*args)[0], 3)
    )


def test_unlimited_and_full_system_are_unchanged():
    for target, cap in ((3, 0), (5, 7), (3, 65)):
        args = (list(range(1, 12)), 5, target, cap, None)
        assert wheel_maxcover(*args) == generate_combinatorial_wheel(*args)


def test_exact_uniform_probability_matches_full_universe_enumeration():
    wheel = [[0, 1, 2], [2, 3, 4]]
    draws = list(combinations(range(8), 3))
    brute = sum(any(len(set(t) & set(d)) >= 2 for t in wheel) for d in draws) / len(
        draws
    )
    assert bench.exact_uniform_rate(wheel, 5, 3, 8, 2) == brute


def test_scoring_cannot_see_target_or_future(monkeypatch):
    draws = np.arange(40).reshape(10, 4)
    seen = []

    def scorer(history, universe):
        seen.append(history.copy())
        return {n: float(n) for n in range(1, universe + 1)}

    monkeypatch.setattr(bench, "score_frequency", scorer)
    for (i, pool, _), prefix in zip(
        list(bench.historical_steps(draws, 49, 11, 7)), seen
    ):
        assert np.array_equal(prefix, draws[:i])
        assert pool == list(range(49, 38, -1))


def test_hit_rates_use_best_ticket_not_pool_union():
    assert bench.ticket_hits([[1, 2, 8], [3, 4, 9]], [1, 2, 3, 4, 5]) == (2, 0, 0)
    assert bench.paired_test([1, 0, 1], [0, 1, 1]) == dict(wins=1, losses=1, p=1.0)
    assert bench.holm([0.01, 0.04, 0.03]) == [0.03, 0.06, 0.06]


def test_same_day_draws_are_not_visible_to_scorer(monkeypatch):
    draws = np.arange(20).reshape(5, 4)
    seen = []

    def scorer(history, universe):
        seen.append(len(history))
        return {n: float(n) for n in range(1, universe + 1)}

    monkeypatch.setattr(bench, "score_frequency", scorer)
    list(bench.historical_steps(draws, 49, 11, 2, [1, 2, 3, 3, 4]))
    assert seen == [2, 2, 4]


def test_dispatcher_and_wf_cache_distinguish_new_method(monkeypatch):
    from wheeling_methods import generate_wheel
    from loto_enterprise.core import walk_forward_adapter as wf

    args = (list(range(1, 12)), 5, 3, 7, None)
    assert generate_wheel("maxcover", *args) == wheel_maxcover(*args)
    monkeypatch.setenv("LOTO_WHEEL_METHOD", "maxcover")
    assert "maxcover" in wf._wheel_sig(11, "joker", 3, None, 7)


def test_worker_does_not_reuse_default_result_for_new_method(monkeypatch):
    import worker

    monkeypatch.delenv("LOTO_WHEEL_METHOD", raising=False)
    default = worker._pipeline_cache_key("same-input")
    monkeypatch.setenv("LOTO_WHEEL_METHOD", " MAXCOVER ")
    assert worker._pipeline_cache_key("same-input") != default
    assert worker._pipeline_cache_key("") == ""
    monkeypatch.delenv("LOTO_WHEEL_METHOD")
    assert worker._pipeline_cache_key("same-input") == default


@pytest.mark.parametrize(
    "game,filename,pick",
    [
        ("6/49", "loto_6_49.csv", 6),
        ("5/40", "loto_5_40.csv", 5),
        ("joker", "joker.csv", 5),
    ],
)
def test_pipeline_retains_pool_and_reports_actual_new_coverage(
    monkeypatch, game, filename, pick
):
    import pandas as pd
    from loto_engine import LotoEngine

    monkeypatch.setattr(LotoEngine, "use_bench_winner", False)
    rows = pd.read_csv("_ISTORIC/" + filename).tail(100).copy()
    results = []
    for method in ("greedy", "maxcover"):
        monkeypatch.setenv("LOTO_WHEEL_METHOD", method)
        engine = LotoEngine(game)
        engine.data = rows.copy()
        engine._build_draw_matrix()
        tickets, *_, context, audit = engine.run_institutional_pipeline(
            pool_size=11, guarantee=3, max_variants=7, track_pool_variation=False
        )
        main_tickets = [ticket[:pick] for ticket in tickets]
        assert context["coverage_pct"] == round(
            100 * len(covered(main_tickets, 3)) / comb(11, 3), 2
        )
        assert len(tickets) == 7
        results.append((engine.hard_core, main_tickets, audit))
    assert results[0][0] == results[1][0]
    assert len(covered(results[1][1], 3)) >= len(covered(results[0][1], 3))
