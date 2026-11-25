"""Causal replay and paired evidence for budget-range analysis."""

import numpy as np
import pandas as pd
import pytest

from covering.lr_budget import ANY_PRIZE, FOUR_PLUS
from covering.lr_pool_ranking import lr_pool_capacity, rank_lr_pool
from covering.lr_selection import resolve_lr_scheme
from loto_enterprise.core import lr_range_analysis as analysis
from loto_enterprise.core.dynamic_pools import ranked_pools
from loto_enterprise.core.dynamic_winner import choose_dynamic_winner
from loto_enterprise.core.history import history_dates, training_cutoffs
from loto_enterprise.core.lr_range_timing import range_timing


def _frame(draws, main=6, maximum=49):
    rng = np.random.default_rng(2107)
    matrix = np.array([rng.choice(np.arange(1, maximum + 1), main, replace=False) for _ in range(draws)])
    frame = pd.DataFrame(matrix, columns=[f"n{i + 1}" for i in range(main)])
    dates = pd.date_range("1996-01-01", periods=draws)
    frame["date"] = dates.strftime("%d-%m-%Y")
    return frame, matrix


def test_batched_ranking_uses_real_labels_on_gapped_bases_at_exact_capacity():
    rng = np.random.default_rng(123)
    for _ in range(25):
        eligible = tuple(sorted(map(int, rng.choice(np.arange(8, 41), 22, replace=False))))
        size = lr_pool_capacity(eligible)
        scores = rng.integers(0, 5, (8, 49))
        result = ranked_pools(scores, pool_size=size, universe=49, eligible_numbers=eligible, max_run=2)
        for row, pool in zip(scores, result, strict=True):
            expected = rank_lr_pool({n: float(row[n - 1]) for n in eligible}, size)
            assert pool.tolist() == sorted(expected)


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
    for limits in (None, (10, 40)):
        base = analysis._eligible(49, limits)
        outside_base = tuple(sorted(set(full) - set(base)))
        direct, _ = choose_dynamic_winner(
            matrix[left:origin], relative, scheme, excluded_numbers=outside_base, max_run=2,
        )
        normal = set(rank_lr_pool(direct, scheme.n))
        for exclusions in ((), normal):
            eligible = tuple(n for n in base if n not in exclusions)
            expected, _ = choose_dynamic_winner(
                matrix[left:origin], relative, scheme, excluded_numbers=tuple(sorted(set(full) - set(eligible))), max_run=2,
            )
            assert pool(eligible).tolist() == sorted(rank_lr_pool(expected, scheme.n))


@pytest.mark.parametrize("inverse", [False, True])
@pytest.mark.parametrize("dynamic", [False, True])
def test_ranges_grade_exact_paid_tickets_with_the_same_policy_on_both_bases(monkeypatch, inverse, dynamic):
    from copy import deepcopy
    from covering.lr_pool_budget import restrict_to_pool
    from loto_enterprise.benchmark import lr_budget_scoring
    from loto_enterprise.core.lr_played_portfolio import grade_plan

    frame, _ = _frame(253)
    frame.loc[251, "date"] = frame.loc[250, "date"]
    calls, expected = [], {}
    def selections(plan, lot, scheme, matrix, full_frame, origins, **settings):
        limits = settings["number_range"]
        calls.append((tuple(origins), limits, settings["inverse"], settings["dynamic"]))
        base = list(analysis._eligible(lot.max_n, limits))
        ranked = sorted(rank_lr_pool(dict.fromkeys(base, 1.), scheme.n))
        if settings["inverse"]:
            ranked = sorted(rank_lr_pool(dict.fromkeys(set(base) - set(ranked), 1.), scheme.n))
        paid = restrict_to_pool(deepcopy(plan), plan["objective"], ranked, ())
        dates = history_dates(frame)
        expected[limits] = [grade_plan(paid, numbers, draw_date=dates.iloc[i].date().isoformat())
                            for i, numbers in enumerate(matrix) if i >= 250]
        return {origin: {"pool": ranked, "jokers": ()} for origin in origins}

    monkeypatch.setattr(lr_budget_scoring, "replay_budget_portfolios", selections)
    analysis._replay.cache_clear()
    result = analysis.compare_budget_ranges(
        171, ANY_PRIZE, "6/49", {"6/49": ("fixture.csv", frame)}, inverse=inverse, dynamic=dynamic,
    )
    assert calls == [((250, 252, 253), limits, inverse, dynamic) for limits in analysis.RANGES]
    for row in result["ranges"]:
        grades = expected[row["range"]]
        assert row["evaluated"] == 3
        assert row["successes"] == sum(g["any_prize"] for g in grades)
        assert row["ticket_3plus"] == sum(g["max_hits"] >= 3 for g in grades)
        assert row["pool_4plus"] == sum(g["pool_hits"] >= 4 for g in grades)


@pytest.mark.parametrize(("amount", "restricted_evaluated"), [(200, 3), (400, 0)])
def test_inverse_metadata_counts_remaining_balls_in_each_base(amount, restricted_evaluated):
    frame, _ = _frame(253)
    report = analysis.compare_budget_ranges(
        amount, FOUR_PLUS, "6/49", {"6/49": ("data.csv", frame)}, inverse=True,
    )
    width = report["pool_size"]
    full, restricted = report["ranges"]
    assert [full["eligible_now"], restricted["eligible_now"]] == [49 - width, 31 - width]
    assert [full["feasible_now"], restricted["feasible_now"]] == [True, bool(restricted_evaluated)]
    assert [full["recent_evaluated"], restricted["recent_evaluated"]] == [3, restricted_evaluated]
    assert restricted["infeasible"] == 3 - restricted_evaluated
    assert "pool-ul direct separat pentru fiecare bază" in report["protocol"]


def test_only_last_100_scored_and_whole_day_excluded_from_first_forecast(monkeypatch):
    frame, matrix = _frame(400)
    # The scored segment starts on row 300, on the same date as the unscored row 299.
    frame.loc[300, "date"] = frame.loc[299, "date"]
    from loto_enterprise.benchmark import lr_budget_scoring
    seen = []
    def selection(plan, lot, scheme, draws, full_frame, origins, **settings):
        assert not settings["dynamic"]
        start = origins[0]
        seen.append((start, draws[start - 1].tolist()))
        low = 10 if settings["number_range"] else 1
        pool = [low + i + i // 2 for i in range(scheme.n)]
        return {day: {"pool": pool, "jokers": ()} for day in origins}
    monkeypatch.setattr(lr_budget_scoring, "replay_budget_portfolios", selection)
    analysis._replay.cache_clear()
    report = analysis.compare_budget_ranges(
        200, FOUR_PLUS, "6/49", {"6/49": ("data.csv", frame)}, dynamic=False,
    )
    assert seen == [(299, matrix[298].tolist())] * 2
    assert report["rows"] == 100 and report["days"] == 100
    assert report["first_date"] == history_dates(frame).iloc[299].date().isoformat()
    assert all(row["evaluated"] == 100 for row in report["ranges"])
    assert all(row["recent_evaluated"] == 25 for row in report["ranges"])
    assert "Selectorul fix" in report["protocol"]


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


def test_undated_range_replay_reports_file_order_and_draw_based_timing():
    frame, _ = _frame(20)
    result = analysis.compare_budget_ranges(
        100, FOUR_PLUS, "6/49", {"6/49": ("undated.csv", frame.drop(columns="date"))}, dynamic=True,
    )
    assert result["first_date"] is result["as_of"] is None
    assert all(row["chronology"]["phase_basis"] == "draws" for row in result["ranges"])
    assert "Se păstrează ordinea rândurilor din fișier" in result["protocol"]
    assert "date calendaristice" in result["reason"]


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
    reference = np.zeros((100, 3), dtype=int)
    candidate = reference.copy()
    candidate[::2] = (4, 4, 1)
    full = analysis._summarize(reference, reference, range_timing(matrix, dates, (1, 49)), 0, None,
                               49, 12)
    good = analysis._summarize(candidate, reference, range_timing(matrix, dates, (10, 40)), 0, (10, 40),
                               31, 12)
    assert good["adjusted_p"] < .05 and good["stable"] and good["phase"]["supports"]
    assert analysis._decision([full, good])[0] == (10, 40)
    for changed in ({"feasible_now": False}, {"infeasible": 1}, {"stable": False},
                    {"phase": {"supports": False}}, {"adjusted_p": .06}):
        assert analysis._decision([full, {**good, **changed}])[0] is None
    assert analysis._decision([{**full, "objective_evaluated": 0}, good])[0] is None


def test_joker_missing_bonus_only_hides_uncertain_prizes_and_does_not_favor_candidate():
    # Known wins and misses are included even when the bonus is unknown.
    # An unknown baseline outcome can still be a prize, limiting the evidence.
    reference = np.asarray([[0, 0, 0], [3, 3, 1], [2, 2, -1], [0, 0, 0]])
    candidate = np.asarray([[3, 3, 1], [0, 0, 0], [3, 3, 1], [2, 2, -1]])
    timing = range_timing(np.tile(np.arange(11, 16), (4, 1)), None, (10, 40))
    row = analysis._summarize(candidate, reference, timing, 0, (10, 40), 31, 10)
    assert (row["successes"], row["objective_evaluated"], row["objective_missing"]) == (2, 3, 1)
    assert (row["paired_evaluated"], row["paired_wins"], row["paired_losses"]) == (2, 1, 1)
    assert row["adjusted_p"] == 1.0


def test_repeated_draws_on_a_day_contribute_one_evidence_factor():
    reference = np.zeros((12, 3), dtype=int)
    candidate = np.tile([4, 4, 1], (12, 1))
    dates = pd.DatetimeIndex(["2026-05-01"] * len(candidate))
    timing = range_timing(np.tile(np.arange(11, 17), (12, 1)), dates, (10, 40))
    row = analysis._summarize(candidate, reference, timing, 0, (10, 40), 31, 12)
    assert row["paired_wins"] == 12
    assert row["adjusted_p"] == pytest.approx(1 / ((1.25 + 1.5) / 2))


def test_single_comparison_uses_reciprocal_of_paired_evidence():
    full = np.zeros((10, 3), dtype=int)
    candidate = full.copy()
    candidate[:6] = (4, 4, 1)
    full[6:8] = (4, 4, 1)
    timing = range_timing(np.tile(np.arange(11, 17), (10, 1)), None, (10, 40))
    row = analysis._summarize(candidate, full, timing, 0, (10, 40), 31, 12)
    evidence = ((1.25 ** 6 * 0.75 ** 2) + (1.5 ** 6 * 0.5 ** 2)) / 2
    assert analysis.RANGES == (None, (10, 40))
    assert row["adjusted_p"] == pytest.approx(1 / evidence)


def test_range_win_on_date_without_reference_counts_only_in_own_rate():
    full = np.asarray([[-1, -1, -1], [4, 4, 1], [0, 0, 0]])
    candidate = np.asarray([[4, 4, 1], [0, 0, 0], [4, 4, 1]])
    timing = range_timing(np.tile(np.arange(11, 17), (3, 1)), None, (10, 40))
    row = analysis._summarize(candidate, full, timing, 0, (10, 40), 31, 12)
    assert row["successes"] == 2 and row["objective_evaluated"] == row["recent_evaluated"] == 3
    assert (row["paired_evaluated"], row["paired_wins"], row["paired_losses"]) == (2, 1, 1)


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
    base_scores, _ = choose_dynamic_winner(matrix[:origin], bounds[:origin], scheme, max_run=2)
    normal = tuple(sorted(rank_lr_pool(base_scores, scheme.n)))
    assert tuple(pool(full)) == normal
    scores, _ = choose_dynamic_winner(matrix[:origin], bounds[:origin], scheme, excluded_numbers=normal, max_run=2)
    assert pool(tuple(n for n in full if n not in normal)).tolist() == sorted(rank_lr_pool(scores, scheme.n))


def test_no_paid_tickets_cannot_contribute_an_objective_win(monkeypatch):
    frame, _ = _frame(253)
    from loto_enterprise.benchmark import lr_budget_scoring
    monkeypatch.setattr(lr_budget_scoring, "replay_budget_portfolios",
                        lambda *args, **kw: {origin: {"pool": [1, 4], "jokers": ()} for origin in args[5]})
    monkeypatch.setattr(analysis, "restrict_to_pool", lambda *args: {"tickets": []})
    analysis._replay.cache_clear()
    result = analysis.compare_budget_ranges(171, FOUR_PLUS, "6/49", {"6/49": ("test.csv", frame)})
    assert result["recommended_range"] is None and "bilete jucabile" in result["reason"]
    assert all(row["infeasible"] == 3 and not row["objective_evaluated"] and not row["feasible_now"]
               for row in result["ranges"])


def test_fixed_share_uses_only_draws_before_each_full_day(monkeypatch):
    from copy import deepcopy
    from loto_enterprise.core import lr_ticket_experts
    frame, _ = _frame(253)
    frame.loc[251, "date"] = frame.loc[250, "date"]
    seen = []
    def fixed_share(lot, plan, scheme, prefix, *, inverse, number_range, objective, **options):
        seen.append((len(prefix), number_range, inverse))
        return deepcopy(plan)
    monkeypatch.setattr(lr_ticket_experts, "online_ticket_forecast", fixed_share)
    analysis._replay.cache_clear()
    result = analysis.compare_budget_ranges(171, FOUR_PLUS, "6/49", {"6/49": ("source.csv", frame)},
                                            inverse=True, selection_policy="online_ticket_experts")
    assert seen == [(origin, limits, True) for limits in analysis.RANGES for origin in (250, 252, 253)]
    assert result["days"] == 2 and "Fixed Share" in result["protocol"]


@pytest.mark.parametrize("inverse", [False, True])
def test_both_ranges_replay_the_same_paid_joker_tickets_as_live_generation(inverse):
    from loto_enterprise.core.lr_budget_forecast import recommend_playable_games
    from loto_enterprise.core.lr_played_portfolio import expand_plan, grade_draw

    frame, _ = _frame(252, main=5, maximum=45)
    # The 15-number inverse Joker scheme needs a playable complement in the 31-ball range.
    rng = np.random.default_rng(57)
    frame.loc[:249, [f"n{i}" for i in range(1, 6)]] = [
        rng.choice(np.arange(10, 40, 2), 5, replace=False) for _ in range(250)
    ]
    frame["joker"] = np.arange(len(frame)) % 20 + 1
    frame.loc[251, "date"] = frame.loc[250, "date"]
    training = {"joker": ("source.csv", frame.iloc[:250])}
    played = []
    for limits in analysis.RANGES:
        plan, = recommend_playable_games(100, ANY_PRIZE, "joker", training, number_range=limits,
                                          inverse=inverse, pool_codes={}, joker_size=5)
        assert plan["forecast"]["selection"]["source"] == "paid_budget"
        played.append(expand_plan(plan))
    # The two official draws on the same date each use a different actual paid variant.
    for i, portfolio in enumerate(played, start=250):
        numbers, joker, _ = portfolio.variants[0]
        frame.loc[i, [f"n{j}" for j in range(1, 6)]] = numbers
        frame.loc[i, "joker"] = joker
    report = analysis.compare_budget_ranges(100, ANY_PRIZE, "joker", {"joker": ("source.csv", frame)},
                                            inverse=inverse, joker_size=5)
    assert report["rows"] == 2 and report["days"] == 1
    for row, portfolio in zip(report["ranges"], played, strict=True):
        expected = [grade_draw(portfolio, r[:5], r[6], draw_date=history_dates(frame).iloc[250].date().isoformat())
                    for r in frame.iloc[250:].itertuples(index=False, name=None)]
        assert row["objective_evaluated"] == 2
        assert row["successes"] == sum(g["any_prize"] for g in expected) >= 1
        assert row["ticket_4plus"] == sum(g["hit4"] for g in expected)


def test_any_prize_uses_categories_in_force_on_each_draw_date(monkeypatch):
    from copy import deepcopy
    from covering.lr_pool_budget import restrict_to_pool
    from loto_enterprise.benchmark import lr_budget_scoring
    from loto_enterprise.core.lr_played_portfolio import expand_plan

    frame, _ = _frame(252)
    frame["date"] = pd.date_range(end="2010-05-02", periods=len(frame)).strftime("%d-%m-%Y")
    plan, = analysis.recommend_games(171, ANY_PRIZE, "6/49")
    scheme = analysis.budget_plan_scheme(plan)
    pool = sorted(rank_lr_pool(dict.fromkeys(range(10, 41), 1.), scheme.n))
    played = expand_plan(restrict_to_pool(deepcopy(plan), ANY_PRIZE, pool, ()))
    variant = played.variants[0][0]
    outside = sorted(set(range(1, 50)).difference(pool))
    frame.loc[250:, [f"n{i}" for i in range(1, 7)]] = list(variant[:3]) + outside[:3]
    monkeypatch.setattr(lr_budget_scoring, "replay_budget_portfolios",
                        lambda *args, **opts: {origin: {"pool": pool, "jokers": ()} for origin in args[5]})
    analysis._replay.cache_clear()
    report = analysis.compare_budget_ranges(171, ANY_PRIZE, "6/49", {"6/49": ("history.csv", frame)})
    assert all(row["ticket_3plus"] == 2 and row["successes"] == 1 and row["objective_evaluated"] == 2
               for row in report["ranges"])
