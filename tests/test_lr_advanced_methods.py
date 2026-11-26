from copy import deepcopy
import json
from math import exp, log

import numpy as np
import pytest

from covering.lr_budget import FOUR_PLUS, recommend_games
from covering.lr_pool_budget import restrict_to_pool
from covering.lr_schemes import scheme_tickets
from covering.lr_selection import resolve_lr_scheme
from loto_enterprise.benchmark import decision, methods
from loto_enterprise.benchmark.methods_online import (
    HAZARD, PRIOR_DRAWS, RUN_LIMIT, _draw_days, _stack_features, RunLengthPosterior, calibration_losses,
)
from loto_enterprise.core.lr_ticket_experts import FixedShareTicketExperts, fixed_share_context, replay_ticket_experts
from loto_enterprise.core.wf_sig import method_cache_semantics


@pytest.mark.parametrize(("universe", "width"), [(49, 6), (40, 6), (45, 5), (20, 1)])
@pytest.mark.parametrize("method", methods.EVALUATION_METHODS)
def test_scoring_is_coherent_and_order_independent_within_a_completed_day(method, universe, width):
    rng = np.random.default_rng(187)
    draws = np.asarray([rng.choice(range(1, universe + 1), width, replace=False) for _ in range(96)])
    cutoffs = np.arange(len(draws))
    cutoffs[35] = 34
    result, _ = methods.call_method(method, draws, universe, history_cutoffs=cutoffs)
    actual = np.array(list(result.values()))
    assert actual.sum() == pytest.approx(width, abs=1e-8)
    assert np.all((0 < actual) & (actual < 1))
    assert np.ptp(actual) > 1e-7
    other = draws.copy()
    other[[34, 35]] = other[[35, 34]]
    again, _ = methods.call_method(method, other, universe, history_cutoffs=cutoffs)
    assert result == again


def test_bocpd_reset_probability_and_predictive_shrinkage_can_be_reproduced_by_hand():
    model = RunLengthPosterior(1 / 3, 3)
    model.observe(np.array([1, 0, 0]), 1)
    first = (np.array([1, 0, 0]) + PRIOR_DRAWS / 3) / (PRIOR_DRAWS + 1)
    np.testing.assert_allclose(model.predict(), HAZARD / 3 + (1 - HAZARD) * first)

    model.observe(np.array([1, 0, 0]), 1)
    reset = HAZARD * (1 / 3) * (2 / 3) ** 2
    grow = (1 - HAZARD) * first[0] * (1 - first[1]) * (1 - first[2])
    np.testing.assert_allclose(model.weights, np.array([reset, grow]) / (reset + grow))
    for _ in range(RUN_LIMIT + 9):
        model.observe(np.array([0, 1, 0]), 1)
    assert len(model.weights) == RUN_LIMIT
    assert np.sum(model.weights) == pytest.approx(1.0)
    assert model.predict().sum() == pytest.approx(1.0)


def test_stack_features_exclude_the_target_day_and_its_outcomes():
    rng = np.random.default_rng(82)
    draws = rng.integers(1, 21, (100, 1), dtype=np.int64)
    dates = np.arange(100)
    dates[52] = 51
    counts, sizes, width = _draw_days(draws, 20, dates)
    features = _stack_features(counts, sizes, width)
    changed = draws.copy()
    changed[51:] = 1
    counts, sizes, width = _draw_days(changed, 20, dates)
    future_features = _stack_features(counts, sizes, width)
    np.testing.assert_array_equal(features[:52], future_features[:52])
    assert np.any(features[52:] != future_features[52:])
    assert calibration_losses([.8, .2], [1]) == pytest.approx({"brier": .04, "log_loss": -log(.8)})


def test_new_scorers_are_versioned_romanian_bench_challengers():
    for method in methods.EVALUATION_METHODS:
        assert methods.method_applies_to_game(method, "joker_urna2")
        assert not methods.method_applies_to_game(method, "de_lotto")
        assert method in decision.EXCLUDED_FROM_PRODUCTION
        assert method_cache_semantics(method)
        assert methods.method_meta(method)["uses_history_cutoffs"]
    methods.require_complete_catalog()


def _plans():
    plan, = recommend_games(100, FOUR_PLUS, "6/49")
    low = [1, 2, 4, 5, 7, 8, 10, 11, 13]
    high = [40, 41, 43, 44, 46, 47, 37, 38, 35]
    return {
        "low": restrict_to_pool(deepcopy(plan), FOUR_PLUS, low),
        "high": restrict_to_pool(plan, FOUR_PLUS, high),
    }


def _winning_draw(plan):
    zone = plan["tickets"][0][0]
    return tuple(scheme_tickets(resolve_lr_scheme(plan["game"], zone["code"]), zone["numbers"])[0])


def test_fixed_share_picks_and_scores_the_real_portfolio_before_learning_from_a_full_day():
    plans = _plans()
    low, high = (_winning_draw(plans[expert]) for expert in ("low", "high"))
    context = fixed_share_context({"budget": 100, "kind": "full"})
    model = FixedShareTicketExperts(("low", "high"), FOUR_PLUS, context=context)
    assert model.choose() == "low"
    record = model.observe("2026-08-02", plans, [(high, None), (high, None)])
    assert record["played_expert"] == "low"
    assert record["losses"] == {"low": 1, "high": 0}
    assert all(grade["hit4"] is False for grade in record["played"])
    assert model.weights[1] == pytest.approx(.95 / (1 + exp(-.7)) + .025)
    assert model.choose() == "high"

    checkpoint = json.loads(json.dumps(model.checkpoint()))
    restored = FixedShareTicketExperts.restore(checkpoint, context=context, history_digest=model.history_digest)
    with pytest.raises(ValueError, match="does not match"):
        FixedShareTicketExperts.restore(checkpoint, context=context, history_digest="changed")
    with pytest.raises(ValueError, match="must follow"):
        restored.observe("2026-08-02", plans, [(high, None)])
    assert restored.observe("2026-08-06", plans, [(low, None)]) == model.observe("2026-08-06", plans, [(low, None)])


def test_replay_passes_one_immutable_prefix_per_complete_day():
    plans = _plans()
    draws = [(_winning_draw(plans["high"]), None)]
    previous = [("2026-07-30", tuple(draws))]
    seen = []

    def plan_before_day(past):
        seen.append(past)
        return plans

    stream = [("2026-08-02", draws * 2), ("2026-08-06", draws)]
    model = FixedShareTicketExperts(("low", "high"), FOUR_PLUS)
    trace = list(replay_ticket_experts(stream, plan_before_day, model, training=previous))
    assert seen == [tuple(previous), (*previous, ("2026-08-02", tuple(draws * 2)))]
    assert [row["played_expert"] for row in trace] == ["low", "high"]


def test_fixed_share_live_and_replay_agree_and_exclude_the_exact_direct_pool():
    import pandas as pd

    from loto_enterprise.core.lotteries import require_lottery
    from loto_enterprise.core.lr_budget_forecast import budget_plan_scheme, recommend_playable_games
    from loto_enterprise.core.lr_ticket_experts import ONLINE_TICKET_EXPERTS, online_ticket_forecast
    from loto_enterprise.core.ranking import longest_consecutive_run

    lot = require_lottery("joker", "RO")
    plan, = recommend_games(100, FOUR_PLUS, lot.game_id)
    scheme = budget_plan_scheme(plan)
    rng = np.random.default_rng(284)
    numbers = [rng.choice(np.arange(1, 46), size=5, replace=False) for _ in range(16)]
    frame = pd.DataFrame(numbers, columns=[f"n{n}" for n in range(1, 6)])
    frame["date"] = pd.date_range("2025-01-01", periods=16).strftime("%Y-%m-%d")
    frame.loc[8, "date"] = frame.loc[7, "date"]
    frame["joker"] = [n % 20 + 1 for n in range(len(frame))]
    frame.loc[12, "joker"] = None
    settings = {"number_range": (5, 40), "lookback": 100, "joker_size": 5}
    inputs = {"pool_codes": {}, "histories": {lot.game_id: ("joker.csv", frame)}}
    direct, = recommend_playable_games(
        100, FOUR_PLUS, lot.game_id, selection_policy=ONLINE_TICKET_EXPERTS, **inputs, **settings,
    )
    inverse, = recommend_playable_games(
        100, FOUR_PLUS, lot.game_id, inverse=True, selection_policy=ONLINE_TICKET_EXPERTS, **inputs, **settings,
    )
    replayed = online_ticket_forecast(lot, plan, scheme, frame, inverse=True, objective=FOUR_PLUS, **settings)
    pools = direct["forecast"], inverse["forecast"]
    assert direct["tickets"] and inverse["tickets"] == replayed["tickets"]
    assert inverse["forecast"]["selection"] == replayed["forecast"]["selection"]
    assert pools[1]["excluded_pool"] == pools[0]["pool"]
    assert pools[1]["excluded_jokers"] == sorted(pools[0]["jokers"])
    assert not set(pools[0]["pool"]) & set(pools[1]["pool"])
    assert not set(pools[0]["jokers"]) & set(pools[1]["jokers"])
    assert all(5 <= num <= 40 for pool in pools for num in pool["pool"])
    assert all(longest_consecutive_run(pool["pool"]) <= 2 for pool in pools)
    assert pools[0]["selection"]["training_days"] == 14
    assert pools[0]["selection"]["training_draws"] == 15
    assert pools[0]["selection"]["skipped_days"] == 0

    from loto_enterprise.core.lr_walk_forward import validate_lr_walk_forward

    result = validate_lr_walk_forward(
        lot.game_id, frame, budget_lei=100, objective=FOUR_PLUS, scheme=scheme.code,
        inverse=True, restricted=True, dynamic=True, lookback=100, hit_target=4, joker_size=5,
        depth=7, selection_policy=ONLINE_TICKET_EXPERTS,
    )
    draw, = result["draws"]
    prior_live, = recommend_playable_games(
        100, FOUR_PLUS, lot.game_id, inverse=True, selection_policy=ONLINE_TICKET_EXPERTS,
        pool_codes={}, histories={lot.game_id: ("joker.csv", frame.iloc[:-1])}, **settings,
    )
    assert draw["tickets"] == prior_live["tickets"]
    assert draw["pool"] == prior_live["forecast"]["pool"]
    assert draw["selected_method"] == prior_live["forecast"]["selection"]

    # Cache values are detached, and a correction even in the oldest row
    # invalidates the forecast's evidence when the latest dates have not changed.
    replayed["forecast"]["pool"].clear()
    repeated = online_ticket_forecast(lot, plan, scheme, frame, inverse=True, objective=FOUR_PLUS, **settings)
    assert repeated["forecast"]["pool"] == pools[1]["pool"]
    frame.loc[0, "n1"], frame.loc[0, "n2"] = frame.loc[0, "n2"], frame.loc[0, "n1"]
    corrected, = recommend_playable_games(
        100, FOUR_PLUS, lot.game_id, selection_policy=ONLINE_TICKET_EXPERTS, **inputs, **settings,
    )
    assert corrected["forecast"]["history_hash"] != pools[0]["history_hash"]
    assert corrected["forecast"]["date"] == pools[0]["date"]


def test_ticket_tuning_uses_completed_days_and_resumes(tmp_path, monkeypatch):
    import pandas as pd
    import runtime_paths
    import loto_enterprise.core.lr_ticket_experts as experts
    from loto_enterprise.core.lotteries import require_lottery
    from loto_enterprise.core.lr_budget_forecast import budget_plan_scheme
    from loto_enterprise.benchmark import lr_provenance

    rng = np.random.default_rng(331)
    draws = [rng.choice(np.arange(1, 50), size=6, replace=False) for _ in range(41)]
    frame = pd.DataFrame(draws, columns=[f"n{n}" for n in range(1, 7)])
    frame["date"] = pd.date_range("2025-01-01", periods=len(frame)).strftime("%Y-%m-%d")
    lot = require_lottery("6/49", "RO")
    plan, = recommend_games(100, FOUR_PLUS, lot.game_id)
    monkeypatch.setattr(runtime_paths, "WF_CACHE_DIR", tmp_path)
    args = lot, plan, budget_plan_scheme(plan), frame
    opts = dict(inverse=False, number_range=None, lookback=100, joker_size=0, objective=FOUR_PLUS)

    class UserCancelled(Exception):
        pass

    calls = 0
    def cancel():
        nonlocal calls
        calls += 1
        if calls == 32:
            raise UserCancelled()

    experts._FORECAST_CACHE.clear()
    with pytest.raises(UserCancelled):
        experts.online_ticket_forecast(*args, **opts, cancelled=cancel)
    assert len(list((tmp_path / "lr_ticket_experts").glob("*.pkl"))) == 1
    experts._expert_scores.cache_clear()
    calls = 0
    resumed = experts.online_ticket_forecast(*args, **opts, cancelled=cancel)
    assert calls == 16  # Initial cancellation check, 14 unfinished dates and the final choice.
    calibrated = resumed["forecast"]["selection"]["calibration"]
    assert calibrated["evaluated_days"] == 14 and len(calibrated["trials"]) == 3
    monkeypatch.setattr(runtime_paths, "WF_CACHE_DIR", tmp_path / "cold")
    experts._FORECAST_CACHE.clear()
    assert experts.online_ticket_forecast(*args, **opts) == resumed
    checkpoint, = (tmp_path / "cold" / "lr_ticket_experts").glob("*.pkl")
    checkpoint.write_bytes(checkpoint.read_bytes()[:10])
    experts._FORECAST_CACHE.clear()
    assert experts.online_ticket_forecast(*args, **opts) == resumed
    # The same method name can acquire a new formula in the registry.
    original = lr_provenance.lr_semantics
    monkeypatch.setattr(lr_provenance, "lr_semantics", lambda *a, **kw: {**original(*a, **kw), "formulas": "changed"})
    calls = 0
    with pytest.raises(UserCancelled):
        experts.online_ticket_forecast(*args, **opts, cancelled=cancel)


def test_missing_joker_preserves_definite_wins_and_reports_unknown_counts():
    from loto_enterprise.core.lr_played_portfolio import PlayedPortfolio, grade_draw

    variants = (((1, 2, 3, 4, 5), 7, 1), ((1, 2, 3, 8, 9), 8, 2))
    paid = PlayedPortfolio("joker", frozenset((1, 2, 3, 4, 5, 8, 9)), variants, 2150, 3)
    for balls in [(1, 2, 3, 4, 12), (1, 2, 13, 14, 15), (11, 12, 13, 14, 15)]:
        partial = grade_draw(paid, balls)
        oracle = [grade_draw(paid, balls, bonus) for bonus in range(1, 21)]
        for field in ("hit4", "pool_hits", "max_hits", "any_prize", "winning_variants", "prize_counts"):
            expected = oracle[0][field] if all(row[field] == oracle[0][field] for row in oracle) else None
            assert partial[field] == expected
        counts = [row["winning_variants"] for row in oracle]
        assert (partial["winning_variants_min"], partial["winning_variants_max"]) == (min(counts), max(counts))
    assert grade_draw(paid, (1, 2, 13, 14, 15))["any_prize"] is None
    assert grade_draw(paid, (11, 12, 13, 14, 15, 3)) == grade_draw(paid, (11, 12, 13, 14, 15), 3)


def test_fixed_share_retries_after_provisional_training(monkeypatch, tmp_path):
    import pandas as pd
    import runtime_paths
    from covering.lr_refinement_status import refinement_complete
    from loto_enterprise.core import lr_ticket_experts as experts
    from loto_enterprise.core.lr_budget_forecast import budget_plan_scheme
    from loto_enterprise.core.lotteries import require_lottery

    lot = require_lottery("6/49", "RO")
    layout, = recommend_games(100, FOUR_PLUS, lot.game_id)
    rng = np.random.default_rng(712)
    frame = pd.DataFrame([rng.choice(np.arange(1, 50), 6, replace=False) for _ in range(9)],
                         columns=[f"n{i}" for i in range(1, 7)])
    frame["date"] = pd.date_range("2024-01-01", periods=len(frame))
    original = experts._candidate_plans
    calls = 0

    def inject_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        plans = original(*args, **kwargs)
        if calls == 1:
            next(iter(plans.values()))["portfolio_refinement"] = {
                "complete": False, "issues": ["refinement_error"], "recipe_sha256": [],
            }
        return plans

    monkeypatch.setattr(experts, "_candidate_plans", inject_once)
    monkeypatch.setattr(runtime_paths, "WF_CACHE_DIR", tmp_path)

    def forecast():
        return experts.online_ticket_forecast(
            lot, layout, budget_plan_scheme(layout), frame, inverse=False, number_range=None,
            lookback=100, joker_size=0, objective=FOUR_PLUS,
        )

    assert not refinement_complete(forecast())
    assert not list(tmp_path.rglob("*.pkl"))
    first_calls = calls
    recovered = forecast()
    assert refinement_complete(recovered)
    assert calls == 2 * first_calls
    assert list(tmp_path.rglob("*.pkl"))
    assert forecast() == recovered
    assert calls == 2 * first_calls
