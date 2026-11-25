"""Causal holdout and official ticket scoring for the LR budget walk-forward."""

from concurrent.futures import CancelledError
import json

import numpy as np
import pandas as pd
import pytest

from covering.lr_budget import ANY_PRIZE, FOUR_PLUS, recommend_games
from covering.lr_pool_budget import restrict_to_pool
from covering.lr_schemes import scheme_tickets
from covering.lr_selection import resolve_lr_scheme
from loto_enterprise.core import lr_walk_forward as wf
from loto_enterprise.core.lr_budget_forecast import recommend_playable_games


def _frame(rows=20, draw_size=6, maximum=49):
    rng = np.random.default_rng(171)
    frame = pd.DataFrame(
        [rng.choice(range(1, maximum + 1), draw_size, replace=False) for _ in range(rows)],
        columns=[f"n{i + 1}" for i in range(draw_size)],
    )
    frame["date"] = pd.date_range("2024-01-01", periods=rows).strftime("%d-%m-%Y")
    return frame


def _run(frame, game="6/49", **kwargs):
    amount = kwargs.get("budget_lei", 171)
    goal = kwargs.get("objective", FOUR_PLUS)
    plan, = recommend_games(amount, goal, game)
    scheme = wf.budget_plan_scheme(plan).code
    settings = {
        "budget_lei": amount, "objective": goal, "scheme": scheme, "inverse": False,
        "restricted": False, "dynamic": True, "lookback": 100, "hit_target": 4,
        "joker_size": 5, "depth": 10, **kwargs,
    }
    return wf.validate_lr_walk_forward(game, ("fixture.csv", frame), **settings)


def _frequency(frame, allowed, count):
    draws = frame.to_numpy().tolist()
    weights = np.exp(np.linspace(-2.0, 0.0, len(draws))).astype(np.float32).tolist()
    tallies = dict.fromkeys(allowed, 0.0)
    for row, weight in zip(draws, weights, strict=True):
        for number in row:
            if number in tallies:
                tallies[number] += weight
    return wf.rank_lr_pool(tallies, count)


@pytest.mark.parametrize("inverse", [False, True])
@pytest.mark.parametrize("restricted", [False, True])
def test_daily_scoring_matches_independent_frequency_and_official_variants(inverse, restricted):
    frame = _frame()
    frame.loc[18, "date"] = frame.loc[17, "date"]
    frame.loc[18, [f"n{i}" for i in range(1, 7)]] = [10, 16, 22, 28, 34, 40]
    frame.loc[19, [f"n{i}" for i in range(1, 7)]] = range(41, 47)
    progress = []
    report = _run(
        frame, inverse=inverse, restricted=restricted, lookback=65,
        progress_cb=lambda done, total: progress.append((done, total)),
    )
    assert report["summary"]["training_rows"] == 17 and report["training_end"] == "2024-01-17"
    assert report["summary"]["rows"] == 3 and report["summary"]["days"] == 2
    assert report["summary"]["evaluated"] == 3 and progress == [(0, 3), (1, 3), (2, 3), (3, 3)]
    expected_in_range = frame.iloc[17:, :6].isin(range(10, 41)).all(axis=1).sum()
    assert report["summary"]["all_main_in_10_40"] == expected_in_range
    assert report["methods"]["tested"]["loto_6_49"]["source"] == "training_frequency"
    assert (report["scheme_code"], report["price_bani"], report["variants"]) == (49, 12050, 15)
    assert report["max_consecutive_run"] == 2
    base = set(range(10, 41) if restricted else range(1, 50))
    assert [row["training_rows"] for row in report["draws"]] == [17, 17, 19]
    for row in report["draws"]:
        origin = row["training_rows"]
        train = frame.iloc[origin-max(1, int(origin*.65)):origin, :6]
        direct = _frequency(train, base, 10) if inverse else []
        expected = sorted(_frequency(train, base.difference(direct), 10))
        assert row["pool"] == expected and row["eligible_count"] == len(base) - len(direct)
        assert row["excluded_pool"] == sorted(direct) and set(direct).isdisjoint(row["pool"])
        actual = set(row["numbers"])
        variants = scheme_tickets(resolve_lr_scheme("6/49", 49), expected)
        assert row["pool_hits"] == len(actual.intersection(expected))
        assert row["ticket_hits"] == max(len(actual.intersection(v)) for v in variants)
        assert row["objective_hit"] == (row["ticket_hits"] >= 4)
    if restricted:
        assert report["draws"][-1]["pool_hits"] == report["draws"][-1]["ticket_hits"] == 0
    assert json.loads(json.dumps(report)) == report


def test_auto_and_both_fitted_decisions_use_only_the_pre_holdout_source(monkeypatch):
    frame = _frame()
    frame.loc[16, "date"] = frame.loc[15, "date"]
    calls = []
    tested = {"members": [{"method": "frequency", "weight": 1.0}], "target": 4, "fallback": False}

    def compare(amount, objective, game, histories, **options):
        source, earlier = histories[game]
        assert source == "fixture.csv"
        assert earlier.index.tolist() == list(range(15))
        calls.append(("auto", options["inverse"]))
        return {"recommended_range": (10, 40), "reason": "Justified in training"}

    def fit(game, training, **options):
        assert training[1].index.tolist() == list(range(15))
        assert options["restricted"] is True and options["scheme"] == 49
        calls.append(("fit", options["inverse"]))
        decision = {**tested, "reference_members": tested["members"]} if options["inverse"] else tested
        return {"urns": [{"game_key": "loto_6_49", "decision": decision}]}

    monkeypatch.setattr(wf, "compare_budget_ranges", compare)
    monkeypatch.setattr(wf, "benchmark_lr_hypotheses", fit)
    result = _run(frame, inverse=True, restricted=None, depth=20)
    assert calls == [("auto", True), ("fit", False), ("fit", True)]
    assert result["requested_restricted"] is None and result["restricted"] is True
    assert result["auto_reason"] == "Justified in training"
    assert result["training_end"] == "2024-01-15" and result["summary"]["rows"] == 5
    assert result["methods"]["tested"]["loto_6_49"]["source"] == "training_rebench"


def test_joker_bonus_is_unchanged_by_main_range_and_prize_needs_matching_variant():
    frame = _frame(rows=40, draw_size=5, maximum=45)
    frame["joker"] = np.arange(len(frame)) % 20 + 1
    frame.loc[39, "joker"] = 0
    full = _run(frame, "joker", objective=ANY_PRIZE, inverse=True, depth=25)
    restricted = _run(frame, "joker", objective=ANY_PRIZE, inverse=True, restricted=True, depth=25)
    plan, = recommend_games(171, ANY_PRIZE, "joker")
    usable_bonus = 0
    for own, partner in zip(restricted["draws"], full["draws"], strict=True):
        if error := own.get("error"):
            assert "fără trei consecutive" in error
            assert own["pool_hits"] is own["joker_hit"] is None
            continue
        usable_bonus += own["joker_drawn"] is not None
        assert own["jokers"] == partner["jokers"] and own["excluded_jokers"] == partner["excluded_jokers"]
        assert set(own["jokers"]).isdisjoint(own["excluded_jokers"])
        scored = set(own["numbers"])
        tickets = restrict_to_pool(dict(plan), ANY_PRIZE, own["pool"], own["jokers"])["tickets"]
        pairs = [
            (len(scored.intersection(v)), zone["joker"])
            for ticket in tickets for zone in ticket
            for v in scheme_tickets(resolve_lr_scheme("joker", zone["code"]), zone["numbers"])
        ]
        best = max(hits for hits, _ in pairs)
        assert own["ticket_hits"] == best
        actual = own["joker_drawn"]
        matching = None if actual is None else any(hits >= 1 and j == actual for hits, j in pairs)
        assert own["ticket_joker_hit"] == matching
        assert own["objective_hit"] == (True if best >= 3 else matching if best >= 1 else False)
    assert restricted["summary"]["joker_evaluated"] == usable_bonus > 0
    assert restricted["draws"][-1]["joker_hit"] is None


def test_540_counts_six_drawn_balls_but_any_prize_requires_four_on_a_five_ball_variant(monkeypatch):
    frame = _frame(maximum=40)
    plan, = recommend_games(171, ANY_PRIZE, "5/40")
    scheme = wf.budget_plan_scheme(plan)
    pool = [n for n in range(1, 38) if n % 3 != 0][:scheme.n]
    tickets = restrict_to_pool(dict(plan), ANY_PRIZE, pool)["tickets"]
    first_zone = tickets[0][0]
    variant = scheme_tickets(resolve_lr_scheme("5/40", first_zone["code"]), first_zone["numbers"])[0]
    sixth = min(set(pool).difference(variant))
    numbers = [f"n{i}" for i in range(1, 7)]
    frame.loc[18, numbers] = [*variant, sixth]
    frame.loc[19, numbers] = [*variant[:3], 38, 39, 40]
    monkeypatch.setattr(wf, "_rank", lambda *args, **kwargs: pool)

    report = _run(frame, "5/40", objective=ANY_PRIZE)
    assert len(variant) == 5
    assert [(row["pool_hits"], row["ticket_hits"], row["objective_hit"]) for row in report["draws"]] == [
        (6, 5, True), (3, 3, False),
    ]
    summary = report["summary"]
    assert (summary["pool_6plus"], summary["ticket_6plus"], summary["ticket_5plus"]) == (1, 0, 1)
    assert (summary["ticket_3plus"], summary["ticket_4plus"], summary["objective_hits"]) == (2, 1, 1)


@pytest.mark.parametrize("restricted", [False, True])
def test_inverse_daily_pool_equals_generation_on_same_prefix_and_fitted_profiles(monkeypatch, restricted):
    frame = _frame()
    frame.loc[18, "date"] = frame.loc[17, "date"]
    decisions = {
        False: {"members": [{"method": "frequency", "weight": 1.0}], "target": 4, "fallback": False},
        True: {"members": [{"method": "markov_lag2", "weight": 1.0}], "target": 4, "fallback": False,
               "reference_members": [{"method": "frequency", "weight": 1.0}]},
    }

    def fit(game, source, **options):
        return {"urns": [{"game_key": "loto_6_49", "decision": decisions[options["inverse"]]}]}

    monkeypatch.setattr(wf, "benchmark_lr_hypotheses", fit)
    result = _run(frame, inverse=True, restricted=restricted, lookback=65)
    lot = wf.require_lottery("6/49", "RO")
    from loto_enterprise.benchmark.lr_provenance import lr_semantics
    template, = recommend_games(171, FOUR_PLUS, "6/49")
    signature = lr_semantics(lot, wf.budget_plan_scheme(template), 4, layout=template)
    for row in result["draws"]:
        prefix = ("fixture.csv", frame.iloc[:row["training_rows"]])
        fingerprint = wf._forecast_engine(lot, prefix, 0).audit["hash"]
        profiles = [
            {
                "game": "6/49",
                "settings": {
                    "scheme": 49, "inverse": inverse, "restricted": restricted,
                    "dynamic": True, "lookback": 65, "hit_target": 4,
                },
                "result": {
                    "history_hash": fingerprint, "semantics": signature,
                    "max_consecutive_run": 2,
                    "urns": [{"game_key": "loto_6_49", "decision": winner}],
                },
            }
            for inverse, winner in decisions.items()
        ]
        generated, = recommend_playable_games(
            171, FOUR_PLUS, "6/49", {"6/49": prefix}, pool_codes={"6/49": 49}, inverse=True,
            number_range=(10, 40) if restricted else None, lookback=65, hit_target=4, lr_rebench_entries=profiles,
        )
        forecast = generated["forecast"]
        assert forecast["selection"]["source"] == "lr_rebench"
        assert forecast["selection"]["method"] == "markov_lag2"
        assert (row["pool"], row["excluded_pool"]) == (forecast["pool"], forecast["excluded_pool"])


def test_depth_100_keeps_initial_day_and_never_splits_it():
    frame = _frame()
    frame.loc[1, "date"] = frame.loc[0, "date"]
    report = _run(frame, depth=100)
    assert report["summary"]["training_rows"] == 2
    assert report["summary"]["first_date"] == "2024-01-03"
    assert report["depth"] == 100 and report["summary"]["rows"] == 18


@pytest.mark.parametrize(("size", "same_day", "training"), [(96, None, 81), (140, 115, 116)])
def test_depth_100_reserves_complete_training_folds_before_all_remaining_days(monkeypatch, size, same_day, training):
    frame = _frame(rows=size)
    if same_day is not None:
        frame.loc[same_day, "date"] = frame.loc[same_day - 1, "date"]
    sizes = []

    def fit(game, source, **kwargs):
        sizes.append(len(source[1]))
        return {"urns": [{"game_key": "loto_6_49", "decision": None}]}

    monkeypatch.setattr(wf, "benchmark_lr_hypotheses", fit)
    result = _run(frame, depth=100, inverse=True)
    assert sizes == [training, training]
    assert result["summary"]["training_rows"] == training
    assert result["summary"]["rows"] == size - training
    assert result["draws"][0]["training_rows"] == training
    assert result["training_end"] < result["summary"]["first_date"]


def test_chronological_validation_refuses_undated_draws():
    with pytest.raises(ValueError, match="cere data fiecărei extrageri"):
        _run(_frame().drop(columns="date"))


def test_joker_requires_at_least_one_valid_prior_bonus_day():
    frame = _frame(draw_size=5, maximum=45)
    frame["joker"] = 0
    frame.loc[len(frame) - 1, "joker"] = 7
    with pytest.raises(ValueError, match="Urna 2 Joker.*niciunei zile"):
        _run(frame, "joker", inverse=True)


def test_infeasible_range_rejected_before_fitting(monkeypatch):
    monkeypatch.setattr(wf, "benchmark_lr_hypotheses", lambda *args, **kwargs: pytest.fail("Fitting an infeasible pool"))
    with pytest.raises(ValueError, match="rămân 15 numere eligibile.*necesită 16"):
        _run(_frame(), budget_lei=400, restricted=True, inverse=True)


def test_cancellation_keeps_the_run_from_finishing(tmp_path):
    marker = tmp_path / "cancel"
    with pytest.raises(CancelledError):
        _run(_frame(), cancel_path=marker, progress_cb=lambda done, total: marker.touch() if done else None)


@pytest.mark.parametrize("mismatch", ["joker_urna1", "joker_urna2"])
def test_frozen_joker_validates_both_inverse_references_before_using_the_paid_winner(monkeypatch, mismatch):
    frame = _frame(draw_size=5, maximum=45)
    frame["joker"] = np.arange(len(frame)) % 20 + 1
    urn_keys = ("joker_urna1", "joker_urna2")
    direct_methods = ("frequency", "compact_transition_prior")
    decision_days = []

    def fit(game, source, **options):
        decision_days.append(source[1]["date"].iloc[-1])
        urns = []
        for key, direct in zip(urn_keys, direct_methods, strict=True):
            decision = {"target": 4 if key == urn_keys[0] else 1, "fallback": False}
            if options["inverse"]:
                method = "markov_lag2"
                reference = [] if key == mismatch == urn_keys[0] else [{
                    "method": "frequency" if key == mismatch else direct, "weight": 1.0,
                }]
                decision["reference_members"] = reference
            else:
                method = direct
            decision["members"] = [{"method": method, "weight": 1.0}]
            urns.append({"game_key": key, "decision": decision})
        return {"urns": urns}

    monkeypatch.setattr(wf, "benchmark_lr_hypotheses", fit)
    report = _run(frame, "joker", inverse=True, budget_lei=100)
    assert decision_days == [frame["date"].iloc[17]] * 2
    assert report["inverse_references_checked"]
    assert report["profile_reference_mismatch"] == [mismatch]
    assert "altă referință" in report["protocol"]
    assert all(report["methods"]["tested"][key]["source"] == "training_frequency" for key in urn_keys)
    for draw in report["draws"]:
        assert set(draw["pool"]).isdisjoint(draw["excluded_pool"])
        assert set(draw["jokers"]).isdisjoint(draw["excluded_jokers"])
        assert len(draw["excluded_jokers"]) == len(draw["jokers"]) == 5
        assert draw["tickets"]
