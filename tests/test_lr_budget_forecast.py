"""Budget recommendations rank the exact LR pool and re-benchmark both eligible urns."""

import pandas as pd
import pytest

from covering.lr_budget import ANY_PRIZE, FOUR_PLUS, recommend_games
from covering.lr_selection import resolve_lr_scheme
from loto_enterprise.core import lr_budget_forecast as forecast


def _history(game="6/49"):
    main = 5 if game == "joker" else 6
    rows = [
        {"date": day, **{f"n{i + 1}": start + i for i in range(main)}}
        for start, day in enumerate(("01-11-2026", "02-11-2026", "02-11-2026", "04-11-2026"), 1)
    ]
    frame = pd.DataFrame(rows)
    if game == "joker":
        frame["joker"] = [4, 21, 12, 4]
    return frame


def _rebench_entry(history, members, *, game="6/49", inverse=False, dynamic=True, lookback=100,
                   reference=(), joker_members=()):
    lot = forecast.require_lottery(game, "RO")
    code = 24 if game == "joker" else 49
    engine = forecast._forecast_engine(lot, ("current.csv", history), 0)
    def decision(key, values, ref=()):
        return {"game_key": key, "decision": {
            "members": [{"method": name, "weight": weight} for name, weight in values],
            "reference_members": [{"method": name, "weight": weight} for name, weight in ref],
            "target": 1 if key == lot.bench_key_urna2 else 4, "evaluated": 82, "folds": [10, 30],
        }}
    urns = [decision(lot.bench_key, members, reference)]
    if joker_members:
        urns.append(decision(lot.bench_key_urna2, joker_members))
    return {"game": game, "settings": {"inverse": inverse, "restricted": True, "dynamic": dynamic,
            "lookback": lookback, "hit_target": 4, "joker_size": 2, "scheme": code},
            "result": {"history_hash": engine.audit["hash"], "scheme_code": code, "urns": urns,
                       "max_consecutive_run": 2}}


@pytest.mark.parametrize("dynamic", [True, False])
def test_saved_rebench_ranks_actual_tickets_and_excludes_current_direct_pool(monkeypatch, dynamic):
    history = _history()
    direct_weights = [("frequency", .3), ("compact_transition_prior", .7)]
    direct = _rebench_entry(history, direct_weights, dynamic=dynamic)
    inverse = _rebench_entry(history, [("compact_renewal_hazard", 1)], inverse=True, dynamic=dynamic,
                             reference=[("frequency", 1)])
    def scores(method, matrix, maximum, **_):
        delta = {"frequency": 0, "compact_transition_prior": 2, "compact_renewal_hazard": 4}[method]
        return {n: n if delta == 0 else ((n + 7) % (3 + delta)) * 19 - n for n in range(1, maximum + 1)}, 0
    monkeypatch.setattr(forecast, "call_method", scores)
    monkeypatch.setattr(forecast, "_dynamic_scores", lambda *a, **k: pytest.fail("reselected"))
    monkeypatch.setattr(forecast, "restricted_bench_scores", lambda *a, **k: pytest.fail("rebench repeated"))
    arguments = dict(pool_codes={}, histories={"6/49": ("current.csv", history)}, number_range=(10, 40),
                     dynamic=dynamic, hit_target=4, lr_rebench_entries=[inverse, direct],
                     generated={"6/49": ("old.csv", {"lr_scheme_code":49, "audit":{"hash":"old"},
                                                     "hard_core":list(range(1, 11))})})
    plan, = forecast.recommend_playable_games(171, FOUR_PLUS, "6/49", **arguments)
    alternative, = forecast.recommend_playable_games(171, FOUR_PLUS, "6/49", inverse=True, **arguments)
    pool, inv = plan["forecast"], alternative["forecast"]
    expected = forecast.combine_ensemble_scores([
        (name, {n: s for n, s in scores(name, None, 49)[0].items() if 10 <= n <= 40}, weight)
        for name, weight in direct_weights
    ])
    assert pool["pool"] == sorted(forecast.rank_lr_pool(expected, 10)) == plan["tickets"][0][0]["numbers"]
    assert pool["selection"]["members"] == direct["result"]["urns"][0]["decision"]["members"]
    assert pool["selection"]["source"] == inv["selection"]["source"] == "lr_rebench"
    assert inv["selection"]["method"] == "compact_renewal_hazard"
    assert inv["eligible_count"] == 21 and inv["excluded_pool"] == pool["pool"]
    assert set(alternative["tickets"][0][0]["numbers"]).isdisjoint(pool["pool"])


def test_matching_inverse_reference_is_used_for_displayed_direct_and_exclusion(monkeypatch):
    history = _history()
    inverse = _rebench_entry(history, [("frequency", 1)], dynamic=False, inverse=True,
                             reference=[("compact_transition_prior", 1)])
    monkeypatch.setattr(forecast, "restricted_bench_scores", lambda *a, **k: pytest.fail("rebench repeated"))
    args = dict(pool_codes={}, histories={"6/49": ("sample.csv", history)}, number_range=(10, 40),
                lr_rebench_entries=[inverse], dynamic=False, hit_target=4)
    direct, = forecast.recommend_playable_games(171, FOUR_PLUS, "6/49", **args)
    other, = forecast.recommend_playable_games(171, FOUR_PLUS, "6/49", inverse=True, **args)
    assert direct["forecast"]["selection"]["source"] == "lr_rebench_reference"
    assert direct["forecast"]["selection"]["method"] == "compact_transition_prior"
    assert other["forecast"]["selection"]["method"] == "frequency"
    assert direct["tickets"][0][0]["numbers"] == other["forecast"]["excluded_pool"]


def test_inverse_only_dynamic_decision_preserves_verified_current_direct_job():
    history = _history()
    inverse = _rebench_entry(history, [("frequency", 1)], inverse=True)
    inverse["settings"]["restricted"] = False
    direct_numbers = [2, 3, 5, 6, 8, 9, 11, 12, 14, 15]
    args = dict(pool_codes={}, histories={"6/49": ("current.csv", history)}, hit_target=4,
                lr_rebench_entries=[inverse], generated={"6/49": ("current.csv", {
                    "hard_core": direct_numbers, "dynamic_method": True, "lr_scheme_code": 49,
                    "audit": {"hash": inverse["result"]["history_hash"]}, "max_consecutive_run": 2,
                })})
    direct, = forecast.recommend_playable_games(171, FOUR_PLUS, "6/49", **args)
    other, = forecast.recommend_playable_games(171, FOUR_PLUS, "6/49", inverse=True, **args)
    assert direct["forecast"]["generated"] and direct["forecast"]["pool"] == direct_numbers
    assert other["forecast"]["selection"]["source"] == "lr_rebench"
    assert other["forecast"]["excluded_pool"] == direct_numbers


@pytest.mark.parametrize("mismatch", [
    "game", "scheme", "dynamic", "lookback", "hit_target", "restricted", "legacy", "history", "custom_range",
    "previous_consecutive_rule",
])
def test_stale_or_incompatible_saved_rebench_keeps_current_selector(monkeypatch, mismatch):
    history = _history()
    entry = _rebench_entry(history, [("frequency", 1)])
    if mismatch == "game":
        entry["game"] = "joker"
    elif mismatch == "legacy":
        del entry["result"]["urns"][0]["decision"]
    elif mismatch == "previous_consecutive_rule":
        del entry["result"]["max_consecutive_run"]
    elif mismatch == "history":
        history.loc[0, "n1"] = 42  # Same dates, last row and number of draws; different full input.
    elif mismatch != "custom_range":
        entry["settings"][mismatch] = {"scheme":57, "dynamic":False, "lookback":75,
                                       "hit_target":3, "restricted":False}[mismatch]
    monkeypatch.setattr(forecast, "_dynamic_scores", lambda *args:
                        ({n: n for n in range(1, 50)}, {"method":"legacy"}))
    plan, = forecast.recommend_playable_games(
        171, FOUR_PLUS, "6/49", {"6/49": ("sample.csv", history)}, pool_codes={}, hit_target=4,
        number_range=(11, 40) if mismatch == "custom_range" else (10, 40), lr_rebench_entries=[entry],
    )
    assert plan["tickets"] and plan["forecast"]["selection"] == {"method":"legacy"}


def test_joker_saved_winners_use_separate_urns_and_valid_bonus_lookback(monkeypatch):
    history = _history("joker")
    args = dict(game="joker", lookback=50, joker_members=[("compact_transition_prior", 1)])
    direct = _rebench_entry(history, [("frequency", 1)], **args)
    inverse = _rebench_entry(history, [("compact_renewal_hazard", 1)], inverse=True, **args)
    calls = []
    def score(method, matrix, maximum, **_):
        calls.append((method, maximum, matrix.tolist()))
        return {n: n for n in range(1, maximum + 1)}, 0
    monkeypatch.setattr(forecast, "call_method", score)
    plan, = forecast.recommend_playable_games(
        100, FOUR_PLUS, "joker", {"joker": ("joker.csv", history)}, pool_codes={}, hit_target=4,
        number_range=(10, 40), lr_rebench_entries=[direct, inverse], lookback=50, joker_size=2, inverse=True,
    )
    record = plan["forecast"]
    assert record["selection"]["method"] == "compact_renewal_hazard"
    assert record["joker_selection"]["method"] == "compact_transition_prior"
    assert [(maximum, len(rows)) for _, maximum, rows in calls] == [(45, 2), (20, 1)] * 2
    assert record["excluded_jokers"] == (19, 20) and record["jokers"] == (18, 17)
    assert plan["tickets"][0][0]["joker"] == 18


@pytest.mark.parametrize(("game", "budget", "objective", "width"), [
    ("6/49", 100, FOUR_PLUS, 9),
    ("6/49", 200, FOUR_PLUS, 12),
    ("6/49", 300, FOUR_PLUS, 16),  # 22 unrestricted positions exceed the catalog.
    ("5/40", 100, FOUR_PLUS, 14),  # Two zones with seven positions each.
    ("joker", 100, FOUR_PLUS, 10),
    ("joker", 100, ANY_PRIZE, 15),
])
def test_budget_scheme_follows_template(game, budget, objective, width):
    plan, = recommend_games(budget, objective, game)
    assert forecast.budget_plan_scheme(plan).n == width


@pytest.mark.parametrize(("former", "budget", "required"), [(59, 100, 48), (48, 200, 57), (58, 200, 57)])
def test_generated_pool_rescored_for_exact_budget_code(monkeypatch, former, budget, required):
    width = resolve_lr_scheme("6/49", former).n
    calls = []

    def scores(game, code, draws, cutoffs, excluded_numbers):
        calls.append((game, code, tuple(excluded_numbers)))
        return {n: n for n in range(1, 50)}, {"method": "test"}

    monkeypatch.setattr(forecast, "_dynamic_scores", scores)
    plan, = forecast.recommend_playable_games(
        budget, FOUR_PLUS, "6/49", {"6/49": ("input.csv", _history())},
        pool_codes={}, generated={"6/49": ("input.csv", {
            "hard_core": list(range(1, width + 1)), "lr_scheme_code": former, "dynamic_method": True,
        })},
    )
    assert calls == [("6/49", required, ())]
    width = resolve_lr_scheme("6/49", required).n
    area, = plan["tickets"][0]
    assert area["code"] == required
    expected = [n for n in range(49, 0, -1) if (49 - n) % 3 != 2][:width]
    assert area["numbers"] == sorted(expected) == plan["forecast"]["pool"]
    assert not plan["forecast"]["generated"]


@pytest.mark.parametrize("dynamic", [True, False])
def test_inverse_excludes_exact_generated_pool_and_keeps_ordinary_available(dynamic):
    original = [2, 3, 5, 6, 8, 9, 11, 12, 14, 15, 17, 18]
    options = dict(
        histories={"6/49": ("input.csv", _history())}, pool_codes={},
        generated={"6/49": ("input.csv", {
            "hard_core": original, "lr_scheme_code": 57,
            "total_draws": 4, "dynamic_method": dynamic, "max_consecutive_run": 2,
        })}, hit_target=4, dynamic=dynamic,
    )
    normal, = forecast.recommend_playable_games(200, FOUR_PLUS, "6/49", **options)
    inverse, = forecast.recommend_playable_games(200, FOUR_PLUS, "6/49", inverse=True, **options)
    assert inverse["forecast"]["excluded_pool"] == normal["tickets"][0][0]["numbers"] == original
    assert inverse["forecast"]["eligible_count"] == 37
    assert inverse["forecast"]["dynamic"] is dynamic
    assert inverse["forecast"]["code"] == normal["forecast"]["code"] == 57
    assert inverse["variants"] == normal["variants"] == 22
    pool = inverse["tickets"][0][0]["numbers"]
    assert len(pool) == 12 and set(pool).isdisjoint(inverse["forecast"]["excluded_pool"])


def test_inverse_resizes_normal_before_exclusion():
    options = dict(
        histories={"6/49": ("input.csv", _history())}, pool_codes={},
        generated={"6/49": ("input.csv", {
            "hard_core": list(range(1, 17)), "lr_scheme_code": 59, "dynamic_method": True,
        })},
    )
    direct, = forecast.recommend_playable_games(100, FOUR_PLUS, "6/49", **options)
    inverse, = forecast.recommend_playable_games(100, FOUR_PLUS, "6/49", inverse=True, **options)
    assert len(inverse["forecast"]["excluded_pool"]) == 9
    assert inverse["forecast"]["excluded_pool"] == direct["tickets"][0][0]["numbers"]
    assert set(inverse["forecast"]["pool"]).isdisjoint(direct["forecast"]["pool"])


def test_joker_inverse_excludes_bonus_and_replays_only_valid_bonus_rows(monkeypatch):
    calls = []

    def restricted(matrix, cutoffs, game_key, size, excluded, target):
        calls.append((matrix.ravel().tolist(), cutoffs, game_key, size, excluded))
        return {n: (99 if n == 2 else n) for n in range(1, 21) if n not in excluded}, {"method": "restricted"}

    monkeypatch.setattr(forecast, "restricted_bench_scores", restricted)
    options = dict(histories={"joker": ("joker.csv", _history("joker"))},
                   pool_codes={}, number_range=(10, 40), joker_size=2, generated={"joker": ("joker.csv", {
                       "hard_core": list(range(1, 11)), "joker_pool": [20, 1], "lr_scheme_code": 24,
                       "dynamic_method": True,
                   })})
    direct, = forecast.recommend_playable_games(100, FOUR_PLUS, "joker", **options)
    plan, = forecast.recommend_playable_games(100, FOUR_PLUS, "joker", inverse=True, **options)
    assert calls == [([4, 12, 4], (0, 1, 2), "joker_urna2", 1, (1, 20))]
    record = plan["forecast"]
    assert record["eligible_joker_count"] == 18
    assert record["excluded_jokers"] == (1, 20)
    assert record["jokers"] == (2, 19)
    assert plan["tickets"][0][0]["joker"] == 2
    assert record["excluded_pool"] == direct["forecast"]["pool"]
    assert record["excluded_jokers"] == tuple(sorted(direct["forecast"]["jokers"]))
    assert set(record["excluded_pool"]).isdisjoint(plan["tickets"][0][0]["numbers"])


def test_joker_impossible_complement_returns_no_ticket():
    plan, = forecast.recommend_playable_games(
        100, FOUR_PLUS, "joker", {"joker": ("joker.csv", _history("joker"))},
        pool_codes={}, inverse=True, joker_size=12, generated={"joker": ("joker.csv", {
            "hard_core": list(range(1, 11)), "joker_pool": list(range(1, 13)), "lr_scheme_code": 24,
        })},
    )
    assert plan["tickets"] == []
    assert "Urna 2 Joker" in plan["forecast_error"] and "8 numere eligibile" in plan["forecast_error"]


@pytest.mark.parametrize("saved", [
    {"audit": {"hash": "different"}},
    {"total_draws": 17},
    {"context": {"last_3": [{"date": "15-11-2026"}]}},
])
def test_inverse_rejects_mismatched_snapshot(saved):
    plan, = forecast.recommend_playable_games(
        200, FOUR_PLUS, "6/49", {"6/49": ("input.csv", _history())}, pool_codes={},
        inverse=True, generated={"6/49": ("input.csv", {
            "lr_scheme_code": 57, "hard_core": list(range(1, 13)), **saved,
        })},
    )
    assert plan["tickets"] == []
    assert "diferă" in plan["forecast_error"]


@pytest.mark.parametrize(("new_dynamic", "new_lookback", "stored_dynamic"), [
    (False, 0, True), (True, 0, False), (True, 50, True),
])
def test_setting_change_recomputes_reference_before_inverse(new_dynamic, new_lookback, stored_dynamic):
    histories = {"6/49": ("settings.csv", _history())}
    options = dict(pool_codes={"6/49": 57}, histories=histories, dynamic=new_dynamic, lookback=new_lookback)
    generated = {"6/49": ("settings.csv", {
        "hard_core": list(range(25, 37)), "lr_scheme_code": 57, "lookback": 0,
        "total_draws": 4, "dynamic_method": stored_dynamic,
    })}
    expected, = forecast.recommend_playable_games(200, FOUR_PLUS, "6/49", **options)
    direct, = forecast.recommend_playable_games(200, FOUR_PLUS, "6/49", generated=generated, **options)
    inverse, = forecast.recommend_playable_games(200, FOUR_PLUS, "6/49", generated=generated, inverse=True, **options)
    assert direct["forecast"]["generated"] is False
    assert direct["forecast"]["pool"] == expected["forecast"]["pool"] == inverse["forecast"]["excluded_pool"]
    assert inverse["forecast"]["rows"] == (2 if new_lookback == 50 else 4)


def test_recovered_history_uses_worker_csv_types_and_hash(tmp_path):
    import io

    from loto_engine import LotoEngine

    uploaded = _history()
    uploaded.index = [7, 9, 5, 3]
    uploaded["n1"] = uploaded["n1"].map(lambda n: f"{n:02}")
    uploaded.loc[9, "n2"] = 50  # Invalid row omitted by both the generating worker and inverse.
    recovered = pd.read_json(io.StringIO(uploaded.to_json(orient="split")), orient="split", dtype=False)
    temp_csv = tmp_path / "worker.csv"
    recovered.to_csv(temp_csv, index=False)
    generator = LotoEngine("6/49", game_key="loto_6_49", country="RO")
    assert generator.load_data(str(temp_csv))
    generated = {"6/49": ("upload.csv", {
        "hard_core": list(range(1, 13)), "lr_scheme_code": 57, "dynamic_method": True,
        "audit": {"hash": generator.audit["hash"]}, "total_draws": 3, "lookback": 0,
    })}
    for budget, width in ((200, 12), (100, 9)):
        record, = forecast.recommend_playable_games(
            budget, FOUR_PLUS, "6/49", {"6/49": ("upload.csv", recovered)},
            pool_codes={}, generated=generated, inverse=True, lookback=100,
        )
        assert len(record["forecast"]["excluded_pool"]) == width
        assert record["forecast"]["rows"] == 3


@pytest.mark.parametrize("dynamic", [True, False])
def test_range_limits_every_selected_main_ball_and_passes_full_union_to_selector(monkeypatch, dynamic):
    calls = []
    def scores(game, code, draws, cutoffs, excluded):
        calls.append(tuple(excluded))
        # Put both ends of the interval at the top of the ranking.
        return {n: 100 if n in (10, 40) else n for n in range(1, 50)}, {"method": "test"}
    def restricted(matrix, cutoffs, game, size, excluded, hit_target, *, strict_lr):
        assert strict_lr
        return scores(game, size, matrix, cutoffs, excluded)
    monkeypatch.setattr(forecast, "_dynamic_scores", scores)
    monkeypatch.setattr(forecast, "restricted_bench_scores", restricted)
    original = [1, 2, 4, 5, 7, 8, 20, 21, 23, 24, 26, 27]
    options = dict(pool_codes={}, dynamic=dynamic, histories={"6/49": ("source.csv", _history())},
                   generated={"6/49": ("source.csv", {"hard_core": original, "lr_scheme_code": 57,
                                                      "dynamic_method": dynamic, "max_consecutive_run": 2})})
    normal, = forecast.recommend_playable_games(200, FOUR_PLUS, "6/49", inverse_range=(10, 40), **options)
    assert normal["forecast"]["pool"] == original and not calls
    direct, = forecast.recommend_playable_games(200, FOUR_PLUS, "6/49", number_range=(10, 40), **options)
    plan, = forecast.recommend_playable_games(200, FOUR_PLUS, "6/49", inverse=True, inverse_range=(10, 40), **options)
    direct_pool = direct["forecast"]["pool"]
    interval = set(range(10, 41))
    eligible = interval - set(direct_pool)
    record = plan["forecast"]
    outside = tuple(sorted(set(range(1, 50)) - interval))
    assert calls == [outside, outside, tuple(sorted(set(range(1, 50)) - eligible))]
    assert record["excluded_pool"] == direct_pool and record["eligible_count"] == len(eligible)
    assert record["inverse_range"] == (10, 40)
    assert set(record["pool"]) <= eligible and {10, 40} <= set(direct_pool)


def test_range_reports_actual_capacity_without_changing_budget_code():
    plan, = forecast.recommend_playable_games(
        300, FOUR_PLUS, "6/49", {"6/49": ("source.csv", _history())}, pool_codes={}, inverse=True,
        inverse_range=(10, 40), generated={"6/49": ("source.csv", {
            "hard_core": list(range(10, 26)), "lr_scheme_code": 59, "dynamic_method": True,
        })},
    )
    assert not plan["tickets"]
    assert "10–40" in plan["forecast_error"] and "15 numere eligibile" in plan["forecast_error"]
    assert "Cod 59 necesită 16" in plan["forecast_error"]


@pytest.mark.parametrize("number_range", [None, (10, 40)])
def test_all_budget_zones_use_disjoint_pool(number_range):
    history = {"5/40": ("draws.csv", _history())}
    options = dict(pool_codes={}, number_range=number_range)
    direct, = forecast.recommend_playable_games(100, FOUR_PLUS, "5/40", history, **options)
    inverse, = forecast.recommend_playable_games(100, FOUR_PLUS, "5/40", history, inverse=True, **options)
    zones = [zone for ticket in inverse["tickets"] for zone in ticket]
    assert [zone["code"] for zone in zones] == [15, 15]
    assert all(len(zone["numbers"]) == 7 for zone in zones)
    played = {n for zone in zones for n in zone["numbers"]}
    assert played == set(inverse["forecast"]["pool"])
    assert played.isdisjoint(direct["forecast"]["pool"])


def test_legacy_string_balls_keep_numeric_order_when_reused():
    normal_pool = [2, 3, 5, 6, 9, 10, 13, 20, 32, 33, 36, 46]
    plan, = forecast.recommend_playable_games(
        200, FOUR_PLUS, "6/49", pool_codes={}, generated={"6/49": ("legacy.csv", {
            "hard_core": [str(n) for n in normal_pool], "lr_scheme_code": "57", "dynamic_method": True,
            "max_consecutive_run": 2,
        })},
    )
    assert plan["forecast"]["generated"]
    assert plan["tickets"][0][0]["numbers"] == normal_pool


@pytest.mark.parametrize(("limits", "ranking", "removed", "added"), [
    ((10, 40), [27, 13, 26, 20, 28, 21, 30, 32, 33, 40, 36], 28, 36),
    (None, [24, 4, 5, 9, 12, 23, 25, 32, 36, 43, 40], 25, 40),
])
def test_ranked_candidates_skip_only_the_third_consecutive_number(monkeypatch, limits, ranking, removed, added):
    scores = dict.fromkeys(range(1, 50), 0.0)
    scores.update({n: len(ranking) - position for position, n in enumerate(ranking)})
    monkeypatch.setattr(forecast, "_dynamic_scores", lambda *args: (scores, {"method": "ranked"}))
    labels = {"6/49": ("test.csv", _history())}
    plan, = forecast.recommend_playable_games(171, FOUR_PLUS, "6/49", labels, pool_codes={}, number_range=limits)
    expected = sorted((set(ranking[:10]) - {removed}) | {added})
    assert plan["forecast"]["pool"] == plan["tickets"][0][0]["numbers"] == expected
    assert plan["forecast"]["max_consecutive_run"] == 2
    assert scores[removed] > scores[added]


def test_legacy_dynamic_pool_is_rescored_before_use_even_when_already_without_triples(monkeypatch):
    legacy = [2, 3, 5, 6, 8, 9, 11, 12, 14, 15]
    captured = []

    def dynamic(matrix, cutoffs, scheme, **settings):
        captured.append(settings)
        return {n: n for n in range(1, 50)}, {"method": "new"}

    monkeypatch.setattr(forecast, "choose_dynamic_winner", dynamic)
    forecast._dynamic_scores.cache_clear()
    try:
        direct, = forecast.recommend_playable_games(
            171, FOUR_PLUS, "6/49", {"6/49": ("test.csv", _history())}, pool_codes={},
            generated={"6/49": ("test.csv", {"hard_core": legacy, "lr_scheme_code": 49, "dynamic_method": True})},
        )
        assert captured == [{"excluded_numbers": (), "max_run": 2}]
        assert not direct["forecast"]["generated"]
        assert direct["forecast"]["pool"] != legacy
    finally:
        forecast._dynamic_scores.cache_clear()
