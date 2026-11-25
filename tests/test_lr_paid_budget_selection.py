"""The LR budget path selects and replays the exact paid variants."""

from copy import deepcopy

import numpy as np
import pandas as pd
import pytest

from covering.lr_budget import ANY_PRIZE, FOUR_PLUS, recommend_games
from covering.lr_pool_budget import restrict_to_pool
from covering.lr_pool_ranking import rank_lr_pool
from loto_enterprise.benchmark import lr_budget_scoring as paid, lr_hypotheses
from loto_enterprise.core import lr_budget_forecast as forecast
from loto_enterprise.core.lotteries import require_lottery
from loto_enterprise.core.lr_played_portfolio import expand_plan, grade_draw
from loto_enterprise.core.lr_walk_forward import validate_lr_walk_forward


def _history(numbers, *, joker=False):
    width = len(numbers[0])
    frame = pd.DataFrame(numbers, columns=[f"n{i+1}" for i in range(width)])
    frame["date"] = pd.date_range("2025-01-01", periods=len(frame)).strftime("%Y-%m-%d")
    frame.loc[105, "date"] = frame.loc[104, "date"]
    if joker:
        frame["joker"] = np.arange(len(frame)) % 20 + 1
    return frame


@pytest.mark.parametrize("dynamic", [True, False])
def test_no_profile_selects_the_better_paid_portfolio_when_pool_hits_tie(monkeypatch, dynamic):
    losing = [6, 9, 10, 13, 20, 21, 24, 27, 30, 40]
    winning = [8, 10, 20, 30, 35, 40, 41, 43, 46, 47]
    drawn = [10, 20, 30, 40, 48, 49]
    frame = _history([drawn] * 115)
    target_method = "compact_transition_prior"
    monkeypatch.setattr(paid, "CANDIDATES", ("frequency", target_method))

    def score(semantics, methods, maximum, draw_n, payload, cutoffs):
        assert len(payload) // (4 * draw_n) == len(cutoffs)
        return {
            method: {n: int(n in (winning if method == target_method else losing)) for n in range(1, maximum + 1)}
            for method in methods
        }

    monkeypatch.setattr(paid, "_live_recipe_scores", score)
    forecast._live_budget_forecast.cache_clear()

    layout, = recommend_games(171, FOUR_PLUS, "6/49")
    a, b = [grade_draw(
        expand_plan(restrict_to_pool(deepcopy(layout), FOUR_PLUS, pool)), drawn, draw_date="2025-05-01",
    ) for pool in (losing, winning)]
    assert a["pool_hits"] == b["pool_hits"] == 4
    assert a["hit4"] is False and b["hit4"] is True

    options = dict(dynamic=dynamic, pool_codes={}, histories={"6/49": ("six.csv", frame)})
    normal, = forecast.recommend_playable_games(171, FOUR_PLUS, "6/49", **options)
    inverse, = forecast.recommend_playable_games(171, FOUR_PLUS, "6/49", inverse=True, **options)
    choice = normal["forecast"]["selection"]
    assert choice["source"] == "paid_budget" and choice["method"] == target_method
    assert choice["portfolio_hits"] == choice["validation_draws"]
    assert choice["random_portfolio_hits"] == 0
    assert normal["forecast"]["pool"] == inverse["forecast"]["excluded_pool"] == winning
    assert set(inverse["forecast"]["pool"]).isdisjoint(winning)

    # Identical setup through the ordinary daily replay entry point. The
    # recorded generation for a test day equals a live call with just its prefix.
    report = validate_lr_walk_forward(
        "6/49", frame, budget_lei=171, objective=FOUR_PLUS, scheme=49, inverse=True,
        restricted=False, dynamic=dynamic, lookback=100, hit_target=4, joker_size=5,
        depth=3, replay_policy="daily",
    )
    for row in report["draws"]:
        prefix = frame.iloc[:row["training_rows"]]
        live, = forecast.recommend_playable_games(
            171, FOUR_PLUS, "6/49", {"6/49": ("six.csv", prefix)},
            dynamic=dynamic, pool_codes={}, inverse=True, hit_target=4,
        )
        assert row["tickets"] == live["tickets"]
        assert row["price_bani"] == live["price_bani"]
        assert row["excluded_pool"] == live["forecast"]["excluded_pool"]
        assert row["selected_method"] == live["forecast"]["selection"]
    forecast._live_budget_forecast.cache_clear()


def test_scoped_inverse_joker_and_unsaved_selector_share_the_paid_reference(monkeypatch):
    rng = np.random.default_rng(4953)
    frame = _history([rng.choice(range(1, 46), 5, replace=False) for _ in range(114)], joker=True)
    lot = require_lottery("joker", "RO")
    template, = recommend_games(100, FOUR_PLUS, lot.game_id)
    scheme = forecast.budget_plan_scheme(template)
    signed_inverse = {"main": "compact_multiscale_shrink", "bonus": "frequency"}

    def _urn(name, matrix, game, *options, **kwargs):
        method = signed_inverse["bonus" if game.is_single_pick else "main"]
        target = 1 if game.is_single_pick else 4
        return {
            "name": name, "game_key": game.key, "target": target, "protocol": "",
            "rows": [{"method": method}, {"method": "frequency"}],
            "decision": {"members": [{"method": method, "weight": 1.0}], "target": target},
        }

    monkeypatch.setattr(lr_hypotheses, "_bench_urn", _urn)
    settings = dict(scheme=scheme.code, inverse=True, restricted=True, dynamic=True, lookback=100,
                    hit_target=4, joker_size=5)
    result = lr_hypotheses.benchmark_lr_hypotheses(
        lot.game_id, frame, **settings, budget_lei=100, objective=FOUR_PLUS,
    )
    assert result["portfolio"]["first_date"] < result["as_of"]
    assert all(urn["decision"]["reference_policy"] == paid.LIVE_BUDGET_REFERENCE for urn in result["urns"])
    prof = {"game": lot.game_id, "settings": settings, "result": result}
    options = dict(pool_codes={}, histories={lot.game_id: ("joker.csv", frame)}, dynamic=True, hit_target=4,
                   number_range=(10, 40), joker_size=5)
    normal, = forecast.recommend_playable_games(100, FOUR_PLUS, lot.game_id, **options)
    unsaved, = forecast.recommend_playable_games(100, FOUR_PLUS, lot.game_id, inverse=True, **options)
    saved, = forecast.recommend_playable_games(
        100, FOUR_PLUS, lot.game_id, inverse=True, lr_rebench_entries=[prof], **options,
    )
    assert normal["forecast"]["selection"]["source"] == unsaved["forecast"]["selection"]["source"] == "paid_budget"
    for plan in (unsaved, saved):
        f = plan["forecast"]
        reference = normal["forecast"]
        assert f["excluded_pool"] == reference["pool"]
        assert f["excluded_jokers"] == tuple(sorted(reference["jokers"]))
        assert set(f["pool"]).isdisjoint(reference["pool"])
        assert set(f["jokers"]).isdisjoint(reference["jokers"])
        assert f["date"] == reference["date"]
        assert plan["price_bani"] == normal["price_bani"]
    assert saved["forecast"]["selection"]["source"] == "lr_rebench"
    assert saved["forecast"]["joker_selection"]["source"] == "lr_rebench"
    assert all(row["cost_bani"] == row["n_eval"] * saved["price_bani"] for row in result["portfolio"]["candidates"])


def test_paid_samples_intersect_unknown_joker_outcomes_instead_of_changing_the_denominator():
    layout, = recommend_games(100, ANY_PRIZE, "joker")
    width = forecast.budget_plan_scheme(layout).n
    pools = {name: rank_lr_pool(dict.fromkeys(base, 1.0), width)
             for name, base in (("two", range(1, 24)), ("three", range(24, 46)))}
    jokers = (1, 2, 3, 4, 5)
    variants = {
        name: expand_plan(restrict_to_pool(deepcopy(layout), ANY_PRIZE, pool, jokers)).variants
        for name, pool in pools.items()
    }
    drawn = [*variants["two"][0][0][:2], *variants["three"][0][0][:3]]
    comparison = paid.measure_paid_portfolios(
        layout, pools, {"shared": jokers}, [(drawn, None)], "2025-01-01", "any_prize",
    )
    almost, known = comparison[("two", "shared")], comparison[("three", "shared")]
    assert almost["n_eval"] == known["n_eval"] == 1
    assert (almost["max_hits"], known["max_hits"]) == (2, 3)
    assert almost["objective_evaluated"] == known["objective_evaluated"] == 0
    assert almost["objective_unknown"] == known["objective_unknown"] == 1
    assert paid._same_portfolio_sample([comparison], ("two", "shared"), ("three", "shared"))
    assert not paid._same_portfolio_sample([
        {"a": {"n_eval": 1}, "b": {"n_eval": 0}},
        {"a": {"n_eval": 0}, "b": {"n_eval": 1}},
    ], "a", "b")


def test_paid_prefix_cache_invalidates_formula_revision(monkeypatch):
    values = []

    def score(method, matrix, maximum, **options):
        values.append(len(values) + 1)
        return dict.fromkeys(range(1, maximum + 1), values[-1]), 0

    monkeypatch.setattr(paid, "call_method", score)
    args = (("frequency",), 49, 6, np.arange(1, 7, dtype=np.int32).tobytes(), (0,))
    try:
        assert paid._live_recipe_scores("before", *args)["frequency"][1] == 1
        assert paid._live_recipe_scores("before", *args)["frequency"][1] == 1
        assert paid._live_recipe_scores("after", *args)["frequency"][1] == 2
    finally:
        paid._live_recipe_scores.cache_clear()
