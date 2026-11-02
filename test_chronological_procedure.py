"""Temporal noninterference and real equal-budget geometry contracts."""
from dataclasses import replace
from itertools import combinations
import json
from math import comb

import numpy as np
import pandas as pd
import pytest

from loto_enterprise.core import chronological_evaluation as c
from loto_enterprise.core import ticket_experiment as t
from loto_enterprise.benchmark import decision


def history(count=25, *, joker=False):
    rng = np.random.default_rng(716)
    rows = []
    for i in range(count):
        main = rng.choice(45 if joker else 49, 5 if joker else 6, replace=False) + 1
        row = {"date": (pd.Timestamp("2026-01-01") + pd.Timedelta(days=i)).strftime("%Y-%m-%d")}
        row.update({f"n{j}": int(n) for j, n in enumerate(main, 1)})
        if joker:
            row["joker"] = int(rng.integers(1, 21))
        rows.append(row)
    return pd.DataFrame(rows)


@pytest.fixture
def small_registry(monkeypatch):
    seen = []
    monkeypatch.setattr(c, "METHODS", {m: None for m in ("frequency", "random", "ridge_pooled_feats")})
    def scorer(method, draws, maximum, *, history_cutoffs):
        seen.append((method, draws.copy(), history_cutoffs))
        counts = np.bincount(draws.ravel(), minlength=maximum + 1)
        sign = -1 if method == "random" else 1
        scores = {n: float(sign * counts[n] + n * .0001) for n in range(1, maximum + 1)}
        return scores, 0.
    monkeypatch.setattr(c, "call_method", scorer)
    def wheel(game, pool, scores, budget, guarantee, condition, *, seed, method):
        return [list(x) for x in list(combinations(pool, game.pick))[:budget]], {"top_up": 0}
    monkeypatch.setattr(c, "equal_budget_pool", wheel)
    return seen


def protocol(**kwargs):
    return c.Protocol("loto_6_49", 3, (c.Parameters(10, guarantee=6), c.Parameters(12, lookback=8, guarantee=6)),
                      inner_draws=6, minimum_training=5, probability_samples=100,
                      design_samples=100, universe_proposals=5, ticket_fees_bani=0, **kwargs)


def test_future_draws_cannot_change_any_part_of_frozen_selection(small_registry):
    frame = history()
    before = c.ChronologicalEvaluator(frame, protocol()).freeze_before(20)
    changed = frame.copy()
    for j in range(1, 7):
        changed.loc[20:, f"n{j}"] = j + 30
    after = c.ChronologicalEvaluator(changed, protocol()).freeze_before(20)
    assert before == after
    lines, info = before
    assert len(lines) == 3
    assert max(info["inner_indices"]) < info["training_end_exclusive"] == 20
    assert info["selection_last_date"] < frame.iloc[20]["date"]
    assert set(info["decision"]["ranked_methods"]) <= set(c.METHODS)


def test_same_day_draws_have_identical_predictions_and_selection(small_registry):
    frame = history()
    frame.loc[21, "date"] = frame.loc[20, "date"]
    evaluator = c.ChronologicalEvaluator(frame, protocol())
    first = evaluator.freeze_before(20)
    second = evaluator.freeze_before(21)
    assert first == second
    assert evaluator.cutoffs[20] == evaluator.cutoffs[21] == 20


def test_context_methods_receive_only_aligned_prior_boundaries_and_lookback(small_registry):
    evaluator = c.ChronologicalEvaluator(history(), protocol())
    evaluator.freeze_before(20)
    for _, prefix, cutoffs in small_registry:
        assert len(prefix) == len(cutoffs)
        assert max(cutoffs) < len(prefix)
        assert len(prefix) <= 20
    assert any(len(prefix) == 8 for _, prefix, _ in small_registry)


def test_no_current_decision_or_curated_selection_and_no_global_target_mutation(monkeypatch, small_registry):
    from loto_enterprise.benchmark import curated
    from loto_enterprise.core import method_selector

    def forbidden(*args, **kwargs):
        raise AssertionError("production decision or curated file accessed")
    monkeypatch.setattr(method_selector, "_load_config", forbidden)
    monkeypatch.setattr(curated, "load_per_game", forbidden)
    original = decision.BENCH_HIT_TARGET
    frozen = c.ChronologicalEvaluator(history(), protocol()).freeze_before(20)
    assert decision.BENCH_HIT_TARGET == original
    assert frozen[1]["decision"]["hit_target"] == 4


def test_all_registry_methods_enter_every_inner_selection(monkeypatch, small_registry):
    recorded = []
    actual = c.decide_optimal_config_for_pool
    def capture(frame, *args, **kwargs):
        recorded.append(set(frame["method"]))
        assert kwargs == {"hit_target": 4, "use_curated": False}
        return actual(frame, *args, **kwargs)
    monkeypatch.setattr(c, "decide_optimal_config_for_pool", capture)
    c.ChronologicalEvaluator(history(), protocol()).freeze_before(20)
    assert recorded and all(names == set(c.METHODS) for names in recorded)


def test_failed_method_has_missing_denominator_and_cannot_gain_from_omissions(monkeypatch, small_registry):
    actual = c.call_method
    def failing(method, draws, maximum, **kwargs):
        if method == "ridge_pooled_feats" and len(draws) % 2:
            return {}, 0.
        return actual(method, draws, maximum, **kwargs)
    monkeypatch.setattr(c, "call_method", failing)
    evaluator = c.ChronologicalEvaluator(history(), protocol())
    _, info = evaluator.freeze_before(20)
    assert evaluator.failures
    assert info["method"] != "ridge_pooled_feats"


@pytest.mark.parametrize("scores", [{}, {i: 0. for i in range(1, 50)}, {1: float("nan"), 2: 1.}, {1: 0., 2: float("inf")}])
def test_unusable_scores_never_become_valid_predictions(monkeypatch, small_registry, scores):
    monkeypatch.setattr(c, "call_method", lambda *a, **kw: (scores, 0.))
    with pytest.raises(ValueError, match="no complete eligible"):
        c.ChronologicalEvaluator(history(), protocol()).freeze_before(20)


def test_joker_selection_uses_both_prior_drums(small_registry):
    p = replace(protocol(), game="joker", parameters=(c.Parameters(12, guarantee=5),))
    evaluator = c.ChronologicalEvaluator(history(joker=True), p)
    lines, info = evaluator.freeze_before(20)
    assert len(lines) == p.budget and all(len(line) == 6 for line in lines)
    assert info["joker_decision"]["hit_target"] == 1
    assert any(prefix.shape[1] == 1 for _, prefix, _ in small_registry)


def test_540_uses_all_six_drawn_numbers_but_five_on_variant(small_registry):
    frame = history()
    for j in range(1, 7):
        frame[f"n{j}"] = j
    p = replace(protocol(), game="loto_5_40", parameters=(c.Parameters(19, guarantee=5),))
    evaluator = c.ChronologicalEvaluator(frame, p)
    lines, _ = evaluator.freeze_before(20)
    assert evaluator.draws.shape[1] == 6 and all(len(line) == 5 for line in lines)
    assert all(prefix.shape[1] == 6 for _, prefix, _ in small_registry)


def test_complete_report_same_budget_no_hidden_financial_zero(small_registry):
    report = c.ChronologicalEvaluator(history(), replace(protocol(), ticket_fees_bani=None)).run(outer_draws=2)
    json.dumps(report, allow_nan=False)
    assert report["selection_validation"] == "nested_chronological_procedure"
    assert "no_demonstrated_predictive_advantage" in report["inference"]
    for row in report["rows"]:
        assert {len(tickets) for tickets in row["tickets"].values()} == {3}
        assert {o["variant_cost_bani"] for o in row["outcomes"].values()} == {2400}
        assert all(o["net_bani"] is None for o in row["outcomes"].values())
        assert all(o["cost_recovery_probability"] is None for o in row["probabilities"].values())


def test_cancel_stops_before_scoring_and_short_histories_fail(small_registry):
    evaluator = c.ChronologicalEvaluator(history(), protocol(), cancel_check=lambda: True)
    with pytest.raises(InterruptedError):
        evaluator.freeze_before(20)
    with pytest.raises(ValueError, match="insufficient"):
        c.ChronologicalEvaluator(history(), protocol()).freeze_before(8)


@pytest.mark.parametrize("changes", [{"budget": 0}, {"budget": True}, {"objective": "blend"}, {"ticket_fees_bani": -1},
    {"parameters": (c.Parameters(6),)}, {"parameters": (c.Parameters(12, restrict_base_min=40),)},
    {"parameters": (c.Parameters(12, lookback=1),)}, {"parameters": (c.Parameters(12, recent_penalty_factor=float("nan")),)}])
def test_protocol_rejects_incomparable_or_invalid_designs(changes):
    with pytest.raises((ValueError, TypeError)):
        replace(protocol(), **changes).validate()


def test_real_production_wheel_is_topped_up_without_changing_pool_or_budget():
    game = t.GAMES["loto_6_49"]
    pool = list(range(1, 10))
    lines, audit = t.equal_budget_pool(game, pool, {n: float(n) for n in pool}, 20, 3)
    assert len(t.validate_tickets(game, lines)) == 20
    assert all(set(line) <= set(pool) for line in lines)
    assert audit["wheel_variants"] + audit["top_up"] == 20


def test_explicit_zero_condition_keeps_classic_cover_semantics():
    game = t.GAMES["loto_6_49"]
    pool = list(range(1, 10))
    scores = {n: float(n) for n in pool}
    assert t.equal_budget_pool(game, pool, scores, 20, 4, 0) == t.equal_budget_pool(game, pool, scores, 20, 4, None)


def test_universe_search_is_draw_blind_and_uses_fixed_distinct_budget():
    game = t.GAMES["loto_6_49"]
    lines, audit = t.optimize_universe(game, 12, design_samples=300, proposals=30)
    assert len(t.validate_tickets(game, lines)) == 12
    assert audit["design_covered_after"] >= audit["design_covered_before"]
    assert not audit["global_optimum"]
    assert (lines, audit) == t.optimize_universe(game, 12, design_samples=300, proposals=30)


def test_sample_probability_matches_independent_exhaustive_small_geometry():
    game = t.Game("test", 9, 6, 6)
    tickets = [[1, 2, 3, 4, 5, 6], [4, 5, 6, 7, 8, 9]]
    exact = sum(max(len(set(line) & set(draw)) for line in tickets) >= 4
                for draw in combinations(range(1, 10), 6)) / comb(9, 6)
    result = t.compare_probabilities(game, {"a": tickets, "same": tickets}, samples=20_000)
    estimate = result["a"]["4plus"]
    assert abs(estimate["probability"] - exact) < .02
    assert result["a"] == result["same"]
    assert result["a"]["jackpot_probability_exact"] == 2 / comb(9, 6)


def test_joker_any_prize_requires_correct_ball_for_one_and_two_main_hits():
    game = t.GAMES["joker"]
    draws = np.zeros((2, 45), dtype=np.int8)
    draws[:, [0, 10, 11, 12, 13]] = 1
    actual = t.winning_matrix(game, [[1, 2, 3, 4, 5, 7]], draws, np.array([7, 8]))
    assert actual[:, 0].tolist() == [True, False]
    assert not t.winning_matrix(game, [[1, 2, 3, 4, 5, 7]], draws, np.array([7, 8]), objective="4plus").any()


@pytest.mark.parametrize("game", list(t.GAMES.values()))
def test_random_reference_has_exactly_declared_distinct_variants(game):
    assert len(t.validate_tickets(game, t.random_tickets(game, 100, 17))) == 100


def test_duplicate_variants_and_oversized_budgets_are_refused():
    game = t.GAMES["loto_6_49"]
    with pytest.raises(ValueError, match="duplicate"):
        t.validate_tickets(game, [[1, 2, 3, 4, 5, 6]] * 2)
    with pytest.raises(ValueError, match="budget exceeds"):
        t.random_tickets(game, 2, 0, pool=range(1, 7))
