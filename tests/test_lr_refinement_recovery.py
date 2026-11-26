"""A request grades and issues the same positional recipe, including during recovery."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from threading import Event, local

import pandas as pd

from covering import lr_expanded_templates as templates
from covering import lr_pool_budget as budgets
from covering.lr_budget import FOUR_PLUS, recommend_games
from loto_enterprise.core import lr_budget_forecast as forecast


def test_concurrent_provisional_live_choice_never_replaces_healthy_selection(monkeypatch, tmp_path):
    import runtime_paths

    monkeypatch.setattr(runtime_paths, "WF_CACHE_DIR", tmp_path)
    history = pd.read_csv(Path(__file__).resolve().parents[1] / "_ISTORIC" / "joker.csv")
    dates = pd.to_datetime(history["date"], dayfirst=True)
    historical = history.loc[dates <= "2026-11-22"].copy()
    scope = local()
    waiting, healthy_finished = Event(), Event()
    original = templates._from_certificate

    def temporary(*args, **kwargs):
        if getattr(scope, "failing", False):
            waiting.set()
            if not healthy_finished.wait(30):
                raise AssertionError("Healthy calculation did not finish")
            raise MemoryError("simulated unavailable certificate")
        return original(*args, **kwargs)

    monkeypatch.setattr(templates, "_from_certificate", temporary)
    selector = forecast._live_budget_forecast
    selector.cache_clear()

    def run(failing=False, snapshot=None):
        scope.failing = failing
        result, = forecast.recommend_playable_games(
            300, FOUR_PLUS, "joker", {"joker": ("joker.csv", historical)}, pool_codes={},
            dynamic=True, lookback=100, joker_size=5, inverse=False, hit_target=4,
            paid_snapshot={"joker": snapshot} if snapshot is not None else None,
        )
        assert result.get("tickets"), result.get("forecast_error")
        return result

    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(run, True)
        try:
            assert waiting.wait(30)
            healthy = run()
        finally:
            healthy_finished.set()
        degraded = pending.result(timeout=30)

    assert degraded["price_bani"] == healthy["price_bani"] == 29500
    assert degraded["attained"] == 7862 and healthy["attained"] == 7920
    provisional = degraded["portfolio_refinement"]
    assert not provisional["complete"] and provisional["issues"] == ["refinement_error"]
    assert degraded["forecast"]["portfolio_refinement"] == provisional
    assert healthy["portfolio_refinement"]["complete"]
    assert healthy["forecast"]["portfolio_refinement"] == healthy["portfolio_refinement"]
    assert healthy["portfolio_refinement"]["recipe_sha256"] != provisional["recipe_sha256"]
    assert selector.cache_info().currsize == 1
    warm = run()
    assert selector.cache_info().hits == 1
    # Only the independent control discards its selection cache.
    selector.cache_clear()
    clean = run()
    assert (healthy["forecast"], healthy["tickets"]) == (warm["forecast"], warm["tickets"]) == (
        clean["forecast"], clean["tickets"])
    for outdated in (degraded["forecast"], {k: v for k, v in degraded["forecast"].items()
                                            if k != "portfolio_refinement"}):
        replayed = run(snapshot=outdated)
        assert (replayed["forecast"], replayed["tickets"]) == (clean["forecast"], clean["tickets"])


def test_signed_paid_snapshot_requires_the_same_prefix_and_policy():
    history = pd.read_csv(Path(__file__).resolve().parents[1] / "_ISTORIC" / "loto_6_49.csv")
    frame = history.tail(115).copy()

    def play(data, snapshot=None, **policy):
        result, = forecast.recommend_playable_games(
            100, FOUR_PLUS, "6/49", {"6/49": ("six.csv", data)}, pool_codes={},
            paid_snapshot={"6/49": snapshot} if snapshot is not None else None, **policy,
        )
        assert result["tickets"], result.get("forecast_error")
        return result

    source = play(frame)["forecast"]
    # Applying an older snapshot cannot change a new forecast even at identical geometry.
    for target, options in ((frame.iloc[:-1], {}), (frame, {"number_range": (10, 40)}),
                            (frame, {"inverse": True}), (frame, {"dynamic": False})):
        clean = play(target, **options)
        replay = play(target, snapshot=source, **options)
        assert source["paid_snapshot_sha256"] != clean["forecast"]["paid_snapshot_sha256"]
        assert replay["forecast"] == clean["forecast"] and replay["tickets"] == clean["tickets"]


def test_paid_rebench_profile_must_identify_at_least_one_verified_recipe(monkeypatch):
    lot = forecast.require_lottery("6/49", "RO")
    layout, = recommend_games(100, FOUR_PLUS, "6/49")
    scheme = forecast.budget_plan_scheme(layout)
    recipe = budgets.positional_recipe(layout, scheme.n, 0)
    monkeypatch.setattr(forecast, "lr_semantics", lambda *args, **kwargs: {"version": 1})
    settings = dict(budget_lei=100, restricted=False, dynamic=True, inverse=False, scheme=scheme.code,
                    lookback=100, hit_target=4)
    decision = {"members": [{"method": "frequency", "weight": 1.0}]}
    result = {"history_hash": "signed-history", "semantics": {"version": 1}, "max_consecutive_run": 2,
              "urns": [{"game_key": lot.bench_key, "decision": decision}],
              "portfolio_refinement": recipe.portfolio_refinement}
    def candidates():
        return forecast._rebench_candidates(
            [{"game": lot.game_id, "settings": settings, "result": result}], lot, scheme, None,
            True, 100, 4, 5, layout, lambda width, slots: recipe,
        )
    assert candidates()
    result["portfolio_refinement"]["recipe_sha256"] = []
    assert not candidates()


def test_proven_bound_time_limit_and_cancellation_have_distinct_cache_eligibility():
    initial, = recommend_games(300, FOUR_PLUS, "6/49")
    labels = range(1, 17)
    timed = budgets.restrict_to_pool(deepcopy(initial), FOUR_PLUS, labels, time_limit_s=0)
    cancelled = budgets.restrict_to_pool(deepcopy(initial), FOUR_PLUS, labels, cancelled=lambda: True)
    assert timed["tickets"] and timed["tickets"] == cancelled["tickets"]
    assert timed["portfolio_refinement"]["complete"] and not timed["portfolio_refinement"]["issues"]
    assert cancelled["portfolio_refinement"]["issues"] == ["cancelled"]
    assert not cancelled["portfolio_refinement"]["complete"]
    assert timed["portfolio_refinement"]["recipe_sha256"] == cancelled["portfolio_refinement"]["recipe_sha256"]
    optimal, = recommend_games(100, FOUR_PLUS, "6/49")
    width = forecast.budget_plan_scheme(optimal).n
    bound = budgets.restrict_to_pool(deepcopy(optimal), FOUR_PLUS, range(1, width + 1))
    assert bound["optimal"] and bound["portfolio_refinement"]["complete"]


def test_base_search_error_keeps_verified_incumbent_without_memoizing(monkeypatch):
    initial, = recommend_games(300, FOUR_PLUS, "6/49")
    width = forecast.budget_plan_scheme(initial).n
    original = budgets._PortfolioSearch.search
    failing = [True]

    def transient(search, incumbent):
        if failing[0]:
            raise MemoryError("simulated base search failure")
        return original(search, incumbent)

    monkeypatch.setattr(budgets._PortfolioSearch, "search", transient)
    first = budgets.positional_recipe(initial, width, 0)
    assert not first.complete and first.issues == ("search_error",)
    assert first.place(range(1, width + 1))["tickets"]
    failing[0] = False
    recovered = budgets.positional_recipe(initial, width, 0)
    assert recovered.complete
    assert recovered.place(range(1, width + 1))["attained"] == 383414
