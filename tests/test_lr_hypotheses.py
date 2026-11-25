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
    assert json.loads(json.dumps(result)) == result


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

    def reference(matrix, bounds, game, width, blocked, target):
        calls.append((len(matrix), width, set(blocked), target))
        return {}, {"method": "frequency"}

    monkeypatch.setattr(bench, "restricted_bench_scores", reference)
    urn, = _run(frame, inverse=True)["urns"]
    assert calls == [(79, 9, set(range(1, 10)) | set(range(41, 50)), 4)]
    assert urn["eligible_count"] == 22
    assert "fixată pe datele disponibile înaintea primei zile" in urn["protocol"]


def test_infeasible_inverse_range_preserves_true_capacity():
    urn, = _run(_history(), scheme=59, inverse=True)["urns"]
    assert (urn["pool_size"], urn["eligible_count"], urn["rows"]) == (16, 15, [])
    assert "Sunt necesare 16" in urn["error"]


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
    _, too_many = _run(frame, "joker", scheme=24, inverse=True, joker_size=11)["urns"]
    assert (too_many["eligible_count"], too_many["rows"]) == (9, [])
    assert "Sunt necesare 11" in too_many["error"]


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
