"""Re-Bench runs the selected LR hypothesis on strictly earlier histories."""

from concurrent.futures import CancelledError
import json

import numpy as np
import pandas as pd
import pytest

from loto_enterprise.benchmark import lr_hypotheses as bench


def _history(rows=100, draw_size=6, maximum=49):
    rng = np.random.default_rng(514)
    draws = [rng.choice(np.arange(1, maximum + 1), draw_size, replace=False) for _ in range(rows)]
    frame = pd.DataFrame(draws, columns=[f"n{i + 1}" for i in range(draw_size)])
    frame["date"] = pd.date_range("2024-01-01", periods=rows).strftime("%d-%m-%Y")
    return frame


def _run(history, game="6/49", **settings):
    settings = {
        "scheme": 48, "inverse": False, "restricted": True, "dynamic": False,
        "lookback": 100, "hit_target": 4, "joker_size": 5, **settings,
    }
    return bench.benchmark_lr_hypotheses(game, ("test.csv", history), **settings)


@pytest.fixture(autouse=True)
def _keep_two_methods(monkeypatch):
    monkeypatch.setattr(bench, "_methods", lambda game_key: ("frequency", "random"))


def test_range_bench_counts_draws_outside_interval_among_the_targets():
    frame = _history()
    frame.loc[80:, ["n1", "n2", "n3", "n4", "n5", "n6"]] = [41, 42, 43, 44, 45, 46]
    result = _run(frame)
    urn, = result["urns"]
    assert (result["scheme_code"], result["inverse"], result["restricted"]) == (48, False, True)
    assert (urn["pool_size"], urn["eligible_count"], urn["target"]) == (9, 31, 4)
    assert urn["folds"] == [10, 100]
    assert {row["method"] for row in urn["rows"]} == {"random", "frequency"}
    assert all((row["evaluated"], row["hit_rate"], row["avg_hits"]) == (20, 0.0, 0.0) for row in urn["rows"])
    assert urn["game_key"] == "loto_6_49" and result["history_rows"] == 100
    assert urn["decision"] == {
        "members": [{"method": "frequency", "weight": 1.0}],
        "target": 4, "fallback": True, "evaluated": 20, "folds": [10, 100],
        "history_rows": 100, "reference_members": [],
    }
    assert json.loads(json.dumps(result)) == result


def test_undated_bench_states_that_the_csv_row_order_is_used():
    report = _run(_history().drop(columns="date"), inverse=True)
    urn, = report["urns"]
    assert report["as_of"] is None
    assert "rândurile anterioare, în ordinea fișierului" in urn["protocol"]
    assert "înaintea primei extrageri evaluate" in urn["protocol"]


@pytest.mark.parametrize(("restricted", "first_ball", "width"), [(False, 1, 49), (True, 10, 31)])
def test_dynamic_inverse_uses_a_direct_pool_from_its_base_without_same_day_leakage(
    monkeypatch, restricted, first_ball, width,
):
    frame = _history()
    frame.loc[83, "date"] = frame.loc[82, "date"]
    direct_origins, candidate_windows = [], []

    def predictor(matrix, bounds, days, scheme, universe, indicators, lookback):
        assert (scheme.n, universe, lookback) == (9, 49, 80)
        assert bounds[83] == 82

        def prepare(origin):
            direct_origins.append(origin)

            def direct(base):
                assert base == tuple(range(first_ball, first_ball + width))
                return base[:scheme.n]
            return direct
        return prepare

    def scorer(name, past, maximum, *, history_cutoffs):
        candidate_windows.append((name, len(past), history_cutoffs))
        return {n: float(n) for n in range(1, maximum + 1)}, 0

    monkeypatch.setattr(bench, "_dynamic_predictor", predictor)
    monkeypatch.setattr(bench, "call_method", scorer)
    urn, = _run(frame, inverse=True, restricted=restricted, dynamic=True, lookback=80)["urns"]
    assert direct_origins == [80, 81, 82, *range(84, 100)]
    assert len(candidate_windows) == 2 * len(direct_origins)
    expected_windows = [o - bench._left(o, 80) for o in direct_origins]
    assert [size for method, size, _ in candidate_windows if method == "frequency"] == [
        *[size for o, size in zip(direct_origins, expected_windows) if o >= 90],
        *[size for o, size in zip(direct_origins, expected_windows) if o < 90],
    ]
    assert all(size == len(bounds) and bounds[-1] <= size - 1 for _, size, bounds in candidate_windows)
    assert urn["eligible_count"] == width - 9 and all(row["evaluated"] == 20 for row in urn["rows"])


def test_fixed_reference_is_trained_before_the_first_target_and_based_in_range(monkeypatch):
    frame = _history()
    frame.loc[80, "date"] = frame.loc[79, "date"]
    calls = []

    def reference(matrix, bounds, game, width, blocked, target, *, strict_lr):
        assert strict_lr
        calls.append((len(matrix), width, set(blocked), target))
        return {}, {"method": "frequency"}

    monkeypatch.setattr(bench, "restricted_bench_scores", reference)
    urn, = _run(frame, inverse=True)["urns"]
    assert calls == [(79, 9, set(range(1, 10)) | set(range(41, 50)), 4)]
    assert urn["eligible_count"] == 22
    assert "fixată pe datele disponibile înaintea primei zile" in urn["protocol"]
    assert urn["decision"]["reference_members"] == [{"method": "frequency", "weight": 1.0}]


def test_scoped_benchmark_counts_the_playable_pool_instead_of_the_raw_top_n(monkeypatch):
    frame = _history()
    frame.loc[80:, ["n1", "n2", "n3", "n4", "n5", "n6"]] = [38, 35, 32, 2, 4, 6]
    monkeypatch.setattr(bench, "call_method", lambda _, history, maximum, **kwargs:
                        ({n: float(28 if n == 27 else n) for n in range(1, maximum + 1)}, 0.0))
    ties = []
    decide = bench.decision.decide_optimal_config_for_pool
    def capture(folds, *args, **kwargs):
        ties.extend(folds["tiebreak_k9"])
        return decide(folds, *args, **kwargs)
    monkeypatch.setattr(bench.decision, "decide_optimal_config_for_pool", capture)
    report = _run(frame)
    assert report["max_consecutive_run"] == 2
    # Raw 32..40 would hit 32, 35, 38. The legal top nine are
    # 40,39,37,36,34,33,31,30,28 and hit none of these targets.
    assert all(row["evaluated"] == 20 and row["avg_hits"] == 0 for row in report["urns"][0]["rows"])
    assert report["urns"][0]["rows"]
    # Scores at raw ranks nine and ten differ, but 27 and 28 tie for the last legal position.
    assert ties and set(ties) == {1.0}


@pytest.mark.parametrize("all_infeasible", [False, True])
def test_inverse_excludes_unplayable_dates_equally_and_preserves_later_prefixes(monkeypatch, all_infeasible):
    frame = _history(maximum=40)
    frame.loc[83, "date"] = frame.loc[82, "date"]
    leaves_room = tuple(range(10, 40, 2))
    too_compact = (40, 39, 37, 36, 34, 33, 31, 30, 28, 27, 25, 24, 22, 21, 19)
    monkeypatch.setattr(bench, "_dynamic_predictor", lambda *_:
                        lambda origin: lambda _: too_compact if all_infeasible or origin % 2 == 0 else leaves_room)
    lengths = []
    def scorer(_, history, maximum, **kwargs):
        lengths.append(len(history))
        return {n: float(n) for n in range(1, maximum + 1)}, 0.0
    monkeypatch.setattr(bench, "call_method", scorer)
    decision_samples = []
    decide = bench.decision.decide_optimal_config_for_pool
    def capture(folds, *args, **kwargs):
        decision_samples.extend(zip(folds["percentile"], folds["n_test"], folds["n_eval"], strict=True))
        return decide(folds, *args, **kwargs)
    monkeypatch.setattr(bench.decision, "decide_optimal_config_for_pool", capture)
    urn, = _run(frame, game="5/40", scheme=35, inverse=True, dynamic=True)["urns"]
    if all_infeasible:
        assert urn["test_draws"] == urn["infeasible_draws"] == 20 and not lengths
        assert "nu încap 15" in urn["error"] and not urn["rows"]
    else:
        assert (urn["infeasible_days"], urn["infeasible_draws"], urn["playable_draws"]) == (10, 11, 9)
        assert {row["evaluated"] for row in urn["rows"]} == {9}
        assert {(tested, scored) for pct, tested, scored in decision_samples if pct == 100} == {(9, 9)}
        assert "9 din 20" in urn["protocol"] and urn["decision"]["evaluated"] == 9
        assert 82 not in lengths and max(lengths) == 99


def test_infeasible_inverse_range_preserves_true_capacity():
    urn, = _run(_history(), scheme=59, inverse=True)["urns"]
    assert (urn["pool_size"], urn["eligible_count"], urn["rows"]) == (16, 15, [])
    assert "Sunt necesare 16" in urn["error"]
    assert urn["decision"] is None


def test_joker_range_affects_only_first_urn_and_bonus_uses_valid_prior_days():
    frame = _history(draw_size=5, maximum=45)
    frame["joker"] = np.arange(len(frame)) % 20 + 1
    frame.loc[85, "joker"] = 0
    result = _run(frame, "joker", scheme=24, inverse=True, joker_size=5)
    main, bonus = result["urns"]
    assert (main["pool_size"], main["eligible_count"]) == (10, 21)
    assert (bonus["pool_size"], bonus["eligible_count"], bonus["target"]) == (1, 15, 1)
    assert len(bonus["rows"]) == 2
    assert all(row["evaluated"] == 19 for row in bonus["rows"])
    assert (main["game_key"], bonus["game_key"]) == ("joker_urna1", "joker_urna2")
    assert (main["decision"]["history_rows"], bonus["decision"]["history_rows"]) == (100, 99)
    _, too_many = _run(frame, "joker", scheme=24, inverse=True, joker_size=11)["urns"]
    assert (too_many["eligible_count"], too_many["rows"]) == (9, [])
    assert "Sunt necesare 11" in too_many["error"]


def test_reusable_decision_carries_real_ensemble_and_only_evaluated_members():
    records = [
        {"method": method, "percentile": percentile, "failed": failed, "n_eval": count}
        for method, percentile, failed, count in (
            ("frequency", 30, False, 32), ("frequency", 100, False, 81),
            ("markov_lag2", 30, False, 31), ("markov_lag2", 100, False, 79),
            ("bench_failure", 30, True, 0), ("bench_failure", 100, True, 0),
            ("random", 30, False, 32), ("random", 100, False, 82),
        )
    ]
    selected = [{"method": "frequency", "weight": 0.75}, {"method": "markov_lag2", "weight": 0.25}]
    reusable = bench._reusable_decision({"ensemble": selected}, records, 5, 162, [])
    assert reusable["members"] == selected
    assert (reusable["evaluated"], reusable["folds"], reusable["target"]) == (79, [30, 100], 5)
    assert reusable["fallback"] is False

    invalid = {"method": "bench_failure", "weight": 0.50}
    partially_failed = bench._reusable_decision(
        {"ensemble": [{"method": "frequency", "weight": 0.50}, invalid]}, records, 5, 162, [],
    )
    assert partially_failed["members"] == [{"method": "frequency", "weight": 1.0}]
    assert partially_failed["fallback"] is True
    assert bench._reusable_decision({"ensemble": [invalid]}, records, 5, 162, []) is None
    assert bench._reusable_decision({"error": "Failed", "ensemble": selected}, records, 5, 162, []) is None


def test_history_fingerprint_validates_normalized_full_source_including_joker():
    frame = _history(draw_size=5, maximum=45)
    frame["joker"] = np.arange(len(frame)) % 20 + 1
    original = _run(frame, "joker", scheme=24, lookback=50)
    shuffled = frame.sample(frac=1, random_state=4).rename(columns={"date": "DATA", "joker": "Joker"})
    normalized = _run(shuffled, "joker", scheme=24, lookback=100)

    assert original["history_hash"] == normalized["history_hash"]
    assert original["history_rows"] == normalized["history_rows"] == 100
    assert original["as_of"] == normalized["as_of"] == "2024-04-09"
    lot = bench.require_lottery("joker", "RO")
    assert original["history_hash"] == bench._forecast_engine(lot, ("test.csv", frame), 0).audit["hash"]

    frame.loc[0, "joker"] = 19
    rectified = _run(frame, "joker", scheme=24, lookback=50)
    assert rectified["history_hash"] != original["history_hash"]
    assert rectified["as_of"] == original["as_of"]


def test_cancel_before_preparing_history(tmp_path):
    marker = tmp_path / "cancel"
    marker.touch()
    with pytest.raises(CancelledError):
        _run(None, cancel_path=marker)


def test_cancellation_during_scoring_is_not_a_failed_fold(monkeypatch, tmp_path):
    marker = tmp_path / "cancel"
    original = bench.call_method

    def cancel_and_score(*args, **kwargs):
        marker.touch()
        return original(*args, **kwargs)

    monkeypatch.setattr(bench, "call_method", cancel_and_score)
    with pytest.raises(CancelledError):
        _run(_history(), cancel_path=marker)
