"""Causal replay and paired evidence for budget-range analysis."""

import numpy as np
import pandas as pd
import pytest

from covering.lr_budget import ANY_PRIZE, FOUR_PLUS
from covering.lr_selection import resolve_lr_scheme
from loto_enterprise.core import lr_range_analysis as analysis
from loto_enterprise.core.dynamic_winner import choose_dynamic_winner
from loto_enterprise.core.history import history_dates, training_cutoffs
from loto_enterprise.core.lr_range_timing import range_timing
from loto_enterprise.core.ranking import rank_by_score


def _frame(draws, main=6, maximum=49):
    rng = np.random.default_rng(2107)
    matrix = np.array([rng.choice(np.arange(1, maximum + 1), main, replace=False) for _ in range(draws)])
    frame = pd.DataFrame(matrix, columns=[f"n{i + 1}" for i in range(main)])
    dates = pd.date_range("1996-01-01", periods=draws)
    frame["date"] = dates.strftime("%d-%m-%Y")
    return frame, matrix


@pytest.mark.parametrize("lookback", [0, 80])
def test_dynamic_replay_matches_production_pool_for_every_treatment(lookback):
    frame, matrix = _frame(535)
    matrix = matrix.astype(np.int32)
    frame.loc[450, "date"] = frame.loc[449, "date"]
    bounds = np.asarray(training_cutoffs(frame))
    days = np.flatnonzero(bounds == np.arange(len(bounds)))
    flags = np.zeros((len(frame), 49), dtype=np.uint8)
    flags[np.arange(len(frame))[:, None], matrix - 1] = 1
    scheme = resolve_lr_scheme("6/49", 57)
    prepare = analysis._dynamic_predictor(matrix, bounds, days, scheme, 49, flags, lookback)
    origin = 464
    left = analysis._left(origin, lookback)
    relative = np.maximum(0, bounds[left:origin] - left)
    full = tuple(range(1, 50))
    pool = prepare(origin)
    normal = set(pool(full))
    for limits in (None, (10, 40)):
        for exclusions in ((), normal):
            eligible = analysis._eligible(49, limits, exclusions)
            expected, _ = choose_dynamic_winner(
                matrix[left:origin], relative, scheme, excluded_numbers=tuple(sorted(set(full) - set(eligible))),
            )
            assert pool(eligible).tolist() == sorted(rank_by_score(expected, scheme.n))


def test_only_last_100_scored_and_whole_day_excluded_from_first_forecast(monkeypatch):
    frame, matrix = _frame(400)
    # The scored segment starts on row 300, on the same date as the unscored row 299.
    frame.loc[300, "date"] = frame.loc[299, "date"]
    seen = []
    def frozen(history, bounds, game, size, excluded, target):
        seen.append((len(history), history[-1].tolist()))
        return {}, {"method": "frequency"}
    monkeypatch.setattr(analysis, "restricted_bench_scores", frozen)
    analysis._replay.cache_clear()
    report = analysis.compare_budget_ranges(
        200, FOUR_PLUS, "6/49", {"6/49": ("data.csv", frame)}, dynamic=False,
    )
    assert seen == [(299, matrix[298].tolist())]
    assert report["rows"] == 100 and report["days"] == 100
    assert report["first_date"] == history_dates(frame).iloc[299].date().isoformat()
    assert all(row["evaluated"] == 100 for row in report["ranges"])
    assert all(row["recent_evaluated"] == 25 for row in report["ranges"])
    assert "Estimare" in report["protocol"]


def test_six_main_balls_and_real_multiple_lr_zones_at_540():
    frame, _ = _frame(256, maximum=40)
    report = analysis.compare_budget_ranges(
        100, FOUR_PLUS, "5/40", {"5/40": ("540.csv", frame)}, dynamic=True,
    )
    assert report["pool_size"] == 14 and report["rows"] == 6
    assert "șase" in report["protocol"]
    for row in report["ranges"]:
        low, high = row["range"] or (1, 40)
        drawn = frame.iloc[-100:, :6]
        contained = ((drawn >= low) & (drawn <= high)).all(axis=1).sum()
        assert row["chronology"]["occurrences"] == contained
        assert row["ticket_4plus"] <= row["pool_4plus"]
        assert row["ticket_3plus"] <= row["pool_3plus"]


def test_short_history_keeps_full_base_but_reports_available_chronology():
    frame, _ = _frame(20)
    result = analysis.compare_budget_ranges(100, FOUR_PLUS, "6/49", {"6/49": ("short.csv", frame)})
    assert [row["range"] for row in result["ranges"]] == [None, (10, 40)]
    assert result["recommended_range"] is None and result["rows"] == 0
    assert "250" in result["reason"]
    assert result["ranges"][0]["chronology"]["occurrences"] == 20
    assert result["ranges"][0]["chronology"]["window_draws"] == 20


def test_joker_range_comparison_handles_bonus_feasibility_and_distinguishes_objectives():
    frame, _ = _frame(255, main=5, maximum=45)
    frame["joker"] = np.arange(len(frame)) % 20 + 1
    source = {"joker": ("j.csv", frame)}
    with pytest.raises(ValueError, match="Urna 2 Joker.*9 bile eligibile.*11"):
        analysis.compare_budget_ranges(100, FOUR_PLUS, "joker", source, inverse=True, joker_size=11)
    prize = analysis.compare_budget_ranges(100, ANY_PRIZE, "joker", source)
    assert prize["metric"].startswith("Orice premiu Joker")
    assert "Urna 2" in prize["protocol"] and prize["ranges"][0]["objective_evaluated"] == 5
    assert prize["ranges"][0]["successes"] >= prize["ranges"][0]["ticket_3plus"]
    four = analysis.compare_budget_ranges(100, FOUR_PLUS, "joker", source)
    assert four["metric"].startswith("4+")


def test_only_consistent_feasible_temporally_supported_advantage_can_narrow():
    matrix = np.tile(np.arange(11, 17), (100, 1))
    dates = pd.date_range("2022-01-01", periods=100)
    reference = np.zeros((100, 2), dtype=int)
    candidate = reference.copy()
    candidate[::2] = (4, 4)
    full = analysis._summarize(reference, reference, 4, range_timing(matrix, dates, (1, 49)), 0, None,
                               tuple(range(1, 50)), 12)
    good = analysis._summarize(candidate, reference, 4, range_timing(matrix, dates, (10, 40)), 0, (10, 40),
                               tuple(range(10, 41)), 12)
    assert good["adjusted_p"] < .05 and good["stable"] and good["phase"]["supports"]
    assert analysis._decision([full, good])[0] == (10, 40)
    for changed in ({"feasible_now": False}, {"infeasible": 1}, {"stable": False},
                    {"phase": {"supports": False}}, {"adjusted_p": .06}):
        assert analysis._decision([full, {**good, **changed}])[0] is None
    assert analysis._decision([{**full, "objective_evaluated": 0}, good])[0] is None


def test_joker_prizes_require_main_and_bonus_on_same_zone_and_hide_missing_bonus():
    # The first bonus belongs to positions 0 and 1, the second to positions 2 and 3.
    matches = analysis._matched_bonus(np.array([1, 1, 4, 2, 1, 0]), np.array([2, 6, 6, 0, 11, 2]),
                                      [2, 6], (0b0011, 0b1100))
    assert matches.tolist() == [1, 0, 1, -1, 0, 0]
    data = np.column_stack(([1, 1, 1, 2, 3, 0], [1, 1, 1, 2, 3, 0], matches))
    timing = range_timing(np.tile(np.arange(11, 16), (6, 1)), None, (10, 40))
    row = analysis._summarize(data, data, 3, timing, 0, (10, 40), tuple(range(10, 41)), 10, True)
    assert row["successes"] == 3 and row["ticket_3plus"] == 1
    assert row["objective_evaluated"] == 5 and row["objective_missing"] == 1


def test_bonus_predictions_use_prior_valid_days_and_date_specific_inverse(monkeypatch):
    from loto_enterprise.core.lotteries import require_lottery
    captured = []
    def record(matrix, bounds, game, size, excluded, target):
        captured.append((matrix.ravel().tolist(), bounds, game, size, target))
        return {}, {"method": "frequency"}
    monkeypatch.setattr(analysis, "restricted_bench_scores", record)
    lot = require_lottery("joker", "RO")
    draws = np.asarray([5, 0, 3, 5, 5, 3, 20, 20, 20])
    bounds = np.asarray([0, 1, 1, 3, 4, 5, 6, 6, 8])
    normal, _ = analysis._bonus_predictor(draws, bounds, 6, lot, 2, 0, False)
    inverse, _ = analysis._bonus_predictor(draws, bounds, 6, lot, 2, 0, True)
    assert captured[0] == ([5, 3, 5, 5, 3], (0, 1, 2, 3, 4), "joker_urna2", 1, 1)
    assert set(normal(6)) == {3, 5} and set(inverse(6)).isdisjoint(normal(6))
    assert set(inverse(8)).isdisjoint(normal(8))


def test_single_comparison_uses_reciprocal_of_paired_evidence():
    full = np.zeros((10, 2), dtype=int)
    candidate = full.copy()
    candidate[:6] = 4
    full[6:8] = 4
    timing = range_timing(np.tile(np.arange(11, 17), (10, 1)), None, (10, 40))
    row = analysis._summarize(candidate, full, 4, timing, 0, (10, 40), tuple(range(10, 41)), 12)
    evidence = ((1.25 ** 6 * 0.75 ** 2) + (1.5 ** 6 * 0.5 ** 2)) / 2
    assert analysis.RANGES == (None, (10, 40))
    assert row["adjusted_p"] == pytest.approx(1 / evidence)


def test_real_int32_history_reproduces_random_guard_at_known_disagreement():
    from loto_enterprise.core.lotteries import require_lottery

    engine = analysis._forecast_engine(require_lottery("6/49", "RO"), None, 0)
    matrix = engine._draw_matrix
    assert matrix.dtype == np.int32
    bounds = np.asarray(training_cutoffs(engine.data))
    origin = 2601
    assert bounds[origin] == origin
    days = np.flatnonzero(bounds == np.arange(len(bounds)))
    flags = np.zeros((len(matrix), 49), dtype=np.uint8)
    flags[np.arange(len(matrix))[:, None], matrix - 1] = 1
    scheme = resolve_lr_scheme("6/49", 48)
    full = tuple(range(1, 50))
    pool = analysis._dynamic_predictor(matrix, bounds, days, scheme, 49, flags, 0)(origin)
    base_scores, _ = choose_dynamic_winner(matrix[:origin], bounds[:origin], scheme)
    normal = tuple(sorted(rank_by_score(base_scores, scheme.n)))
    assert tuple(pool(full)) == normal
    scores, _ = choose_dynamic_winner(matrix[:origin], bounds[:origin], scheme, excluded_numbers=normal)
    assert pool(analysis._eligible(49, None, normal)).tolist() == sorted(rank_by_score(scores, scheme.n))