"""Independent regressions for the 2026-10-01 audit and exact budget optimization."""

from itertools import combinations, product
from math import comb

import numpy as np
import pandas as pd
import pytest

from budget_cover import wheel_hitcover
from covering.probability import wheel_hit_probabilities, wheel_hit_profile
from loto_engine import LotoEngine
from scripts.analysis import audit_patterns_and_designs as audit
from scripts.analysis import bench_budget_cover as bench
from wheeling_methods import compute_coverage_pct, generate_wheel


def test_hit_profile_matches_independent_exhaustive_intersections():
    pool = list(range(1, 8))
    tickets = [[1, 2, 3], [3, 4, 5], [5, 6, 7]]
    profile = wheel_hit_profile(pool, tickets)
    for size in range(len(pool) + 1):
        intersections = list(map(set, combinations(pool, size)))
        for target in range(len(pool) + 1):
            expected = sum(
                max(len(s & set(t)) for t in tickets) >= target for s in intersections
            )
            assert profile[size][target] == expected


@pytest.mark.parametrize(
    "v,pick,g,budget", list(product((8, 11, 16), (5, 6), (3, 4), (1, 7, 10)))
)
def test_hitcover_preserves_all_hit_thresholds_and_exact_coverage(v, pick, g, budget):
    pool = list(range(2, 2 * v + 1, 2))
    scores = {n: float((n * 17) % 23) for n in pool}
    base, cov = generate_wheel("greedy", pool, pick, g, budget, scores)
    actual, actual_cov = generate_wheel("hitcover", pool, pick, g, budget, scores)
    assert len(actual) == len(base) <= budget
    assert all(len(t) == len(set(t)) == pick and set(t) <= set(pool) for t in actual)
    assert len(set(map(tuple, actual))) == len(actual)
    baseline, candidate = wheel_hit_profile(pool, base), wheel_hit_profile(pool, actual)
    assert all(a >= b for ar, br in zip(candidate, baseline) for a, b in zip(ar, br))
    assert actual_cov >= cov
    assert actual_cov == compute_coverage_pct(actual, pool, g)
    assert wheel_hitcover(pool, pick, g, budget, scores) == (actual, actual_cov)


def test_hitcover_improves_real_ticket_odds_at_equal_cost():
    pool = list(range(1, 12))
    scores = {n: float((n * 17) % 23) for n in pool}
    base, _ = generate_wheel("greedy", pool, 6, 4, 7, scores)
    improved, _ = generate_wheel("hitcover", pool, 6, 4, 7, scores)
    a = wheel_hit_probabilities(pool, base, 6, 49)
    b = wheel_hit_probabilities(pool, improved, 6, 49)
    assert len(base) == len(improved) == 7
    assert b["pool"] == a["pool"]
    assert b["ticket"][3] > a["ticket"][3]
    assert b["ticket"][4] > a["ticket"][4]
    assert all(b["ticket"][t] >= a["ticket"][t] for t in a["ticket"])


@pytest.mark.parametrize("cap,g", [(0, 3), (65, 3), (7, 5)])
def test_hitcover_bounds_preserve_incumbent(cap, g):
    args = (list(range(1, 12)), 5, g, cap, None)
    assert generate_wheel("hitcover", *args) == generate_wheel("greedy", *args)


def test_hitcover_rejects_candidate_that_sacrifices_higher_hits(monkeypatch):
    import budget_cover

    pool = list(range(1, 9))
    incumbent = [[1, 2, 3, 4, 5], [1, 2, 3, 6, 7]]
    proposed = ((0, 1, 2, 3, 5), (0, 1, 2, 4, 6))
    monkeypatch.setattr(
        "covering.greedy.generate_combinatorial_wheel", lambda *a: (incumbent, 50.0)
    )
    monkeypatch.setattr(budget_cover, "maximum_cover_positions", lambda *a: proposed)
    # Force an adversarial candidate profile: more 3-hits, but fewer 4-hits.
    baseline = wheel_hit_profile(pool, incumbent)
    bad = [list(row) for row in baseline]
    bad[3][3] += 1
    bad[4][4] -= 1
    calls = []

    def profile(*args):
        calls.append(args)
        return baseline if len(calls) == 1 else tuple(map(tuple, bad))

    monkeypatch.setattr("covering.probability.wheel_hit_profile", profile)
    result, _ = wheel_hitcover(pool, 5, 3, 2)
    assert result == incumbent


@pytest.mark.parametrize(
    "pool,tickets,draw_n,max_num",
    [
        ([1, 2, 3.5], [[1, 2]], 2, 8),
        ([1, 2, 3], [[1, 2.9]], 2, 8),
        ([1, 2, 3], [[1, 2]], 2.5, 8),
        ([1, 2, 3], [[1, 2]], 2, 8.5),
        ([1, 2, 3], [[1, 2]], 2, float("inf")),
    ],
)
def test_fractional_probability_inputs_are_rejected(pool, tickets, draw_n, max_num):
    with pytest.raises(ValueError):
        wheel_hit_probabilities(pool, tickets, draw_n, max_num)


def test_joker_second_urn_cannot_hide_an_unplayed_main_pool_number(monkeypatch):
    engine = LotoEngine("joker")
    engine.data = pd.read_csv("_ISTORIC/joker.csv").tail(100).copy()
    engine._build_draw_matrix()
    monkeypatch.setattr(
        engine,
        "_get_timesfm_scores",
        lambda **kw: {n: float(46 - n) for n in range(1, 46)},
    )
    monkeypatch.setattr(
        engine, "generate_predictions", lambda **kw: ([[1, 2, 3, 4, 5, 6]], 100.0)
    )
    *_, audit_result = engine.run_institutional_pipeline(
        pool_size=6, guarantee=3, track_pool_variation=False
    )
    assert engine.hard_core == [1, 2, 3, 4, 5, 6]
    assert audit_result["pool_numbers_not_on_tickets"] == [6]


def test_audit_excludes_all_draws_on_target_day_and_scores_all_six_540(
    tmp_path, monkeypatch
):
    (tmp_path / "_ISTORIC").mkdir()
    dates = pd.date_range("2025-01-01", periods=20).strftime("%d-%m-%Y").tolist()
    dates[11] = dates[10]
    df = pd.DataFrame({f"n{i}": [i] * 20 for i in range(1, 7)})
    df["date"] = dates
    df.to_csv(tmp_path / "_ISTORIC/loto_5_40.csv", index=False)
    monkeypatch.setattr(audit, "ROOT", tmp_path)
    seen = []

    def scorer(history, dates, target, universe):
        seen.append(history.copy())
        return {"frequency": {n: float(n <= 6) for n in range(1, 41)}}

    monkeypatch.setattr(audit, "candidate_scores", scorer)
    result = audit.audit_game("5/40", 6)
    assert [len(seen[i]) for i in (0, 1, 2)] == [10, 10, 12]
    assert all(h.shape[1] == 6 for h in seen)
    assert result["draw_n_scored"] == 6 and result["pick_n"] == 5
    assert result["target"] == 4
    assert result["holdout"]["frequency"]["mean_hits"] == 6


def test_audit_rejects_invalid_sixth_number_540(tmp_path, monkeypatch):
    (tmp_path / "_ISTORIC").mkdir()
    df = pd.DataFrame({"date": ["01-01-2025"], **{f"n{i}": [i] for i in range(1, 7)}})
    df["n6"] = 6.7
    df.to_csv(tmp_path / "_ISTORIC/loto_5_40.csv", index=False)
    monkeypatch.setattr(audit, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="numere invalide"):
        audit.audit_game("5/40", 6)


def test_budget_probability_uses_six_drawn_numbers_on_five_number_ticket():
    tickets = [[0, 1, 2, 3, 4], [0, 1, 2, 5, 6]]
    brute = list(map(set, combinations(range(10), 6)))
    expected = sum(any(len(s & set(t)) >= 4 for t in tickets) for s in brute) / comb(
        10, 6
    )
    assert bench.exact_uniform_rate(tickets, 7, 6, 10, 4) == pytest.approx(expected)
    assert bench.GAMES["5/40"][2:] == (6, 5)


def test_hitcover_is_automatic_and_greedy_override_separates_cache(monkeypatch):
    from loto_enterprise.core import walk_forward_adapter as wf
    import worker

    monkeypatch.delenv("LOTO_WHEEL_METHOD", raising=False)
    default = wf._wheel_sig(11, "6/49", 4, None, 7)
    cache = worker._pipeline_cache_key("input")
    assert default.startswith("hitcover|")
    assert wf._wheel_sig(11, "6/49", 4, None, 0).startswith("lajolla|")
    monkeypatch.setenv("LOTO_WHEEL_METHOD", "greedy")
    assert wf._wheel_sig(11, "6/49", 4, None, 7).startswith("greedy|")
    assert worker._pipeline_cache_key("input") != cache
    monkeypatch.delenv("LOTO_WHEEL_METHOD", raising=False)
    engine = LotoEngine("6/49")
    engine.hard_core = list(range(1, 12))
    scores = {n: float((n * 17) % 23) for n in engine.hard_core}
    assert engine.generate_predictions(4, 7, scores) == generate_wheel(
        "hitcover", engine.hard_core, 6, 4, 7, scores
    )


def test_leaderboard_weighted_ties_match_decision(monkeypatch):
    import ui_bench
    import app_nicegui as app
    from loto_enterprise.benchmark import decision
    from scripts.analysis.audit_output import capture_ui

    rows = []
    for pct, n in ((10, 10), (30, 20), (60, 30), (100, 1000)):
        for name, rate, tie in (
            ("random", 0.085, 0),
            ("frequency", 0.11, 0.99 if n < 1000 else 0.05),
            ("markov_pairs", 0.15, 0.05 if n < 1000 else 0.8),
        ):
            rows.append(
                dict(
                    game="joker_urna1",
                    method=name,
                    percentile=pct,
                    n_eval=n,
                    n_test=n,
                    blocks=n,
                    runtime_sec=0.01,
                    is_random=False,
                    failed=False,
                    k11=1.5,
                    rate_3plus_k11=rate,
                    rate_4plus_k11=0.01,
                    tiebreak_k11=tie,
                )
            )
    monkeypatch.setattr(decision, "BENCH_HIT_TARGET", 3)
    app._LB_ROWS_MEMO.clear()
    frame = pd.DataFrame(rows)
    cfg = decision.decide_optimal_config_for_pool(frame, "joker_urna1", 11, 5)
    assert cfg["scorer"] == "frequency"
    assert (
        ui_bench._bench_structural_exclusion(
            frame.query("method == 'frequency'"), "rate_3plus_k11", 11, set()
        )
        == ""
    )
    with capture_ui() as capture:
        app._render_bench_leaderboard_slice(frame, "joker_urna1", 11, "Joker", 20)
    assert capture.ranking()[0] == "frequency"
    assert "markov_pairs" not in capture.ranking()


def test_leaderboard_partial_evaluations_have_no_rank(monkeypatch):
    import app_nicegui as app
    from loto_enterprise.benchmark import decision
    from scripts.analysis.audit_output import capture_ui

    rows = []
    for pct, n in ((10, 100), (30, 300), (60, 600), (100, 1000)):
        for name, rate in (
            ("random", 0.085),
            ("frequency", 0.11),
            ("markov_pairs", 0.15),
        ):
            rows.append(
                dict(
                    game="joker_urna1",
                    method=name,
                    percentile=pct,
                    n_eval=n - 1 if name == "markov_pairs" else n,
                    n_test=n,
                    runtime_sec=0.01,
                    is_random=False,
                    failed=False,
                    k11=1.5,
                    rate_3plus_k11=rate,
                    rate_4plus_k11=0.01,
                    tiebreak_k11=0.1,
                )
            )
    monkeypatch.setattr(decision, "BENCH_HIT_TARGET", 3)
    app._LB_ROWS_MEMO.clear()
    with capture_ui() as capture:
        app._render_bench_leaderboard_slice(
            pd.DataFrame(rows), "joker_urna1", 11, "Joker", 20
        )
    assert capture.ranking()[0] == "frequency"
    assert "markov_pairs" not in capture.ranking()
    assert "extrageri neevaluate" in capture.text()


def test_output_audit_resolves_foreign_history_from_registry(tmp_path, monkeypatch):
    from pathlib import Path
    from scripts.analysis import audit_output
    from loto_enterprise.core.lotteries import require_lottery

    lot = require_lottery("bg_toto2", "BG")
    target = tmp_path / lot.csv
    target.parent.mkdir(parents=True)
    pd.DataFrame({"date": ["01-01-2025"], **{f"n{i}": [i] for i in range(1, 7)}}).to_csv(target, index=False)
    monkeypatch.setattr(audit_output, "ROOT", tmp_path)
    resolved, frame = audit_output.audit_history(
        Path(lot.csv).name, lot.game_id, {"game_id": lot.game_id, "country": lot.country}
    )
    assert resolved == lot and len(frame) == 1
    assert resolved.draw_n == resolved.pick_n == 6


def test_output_audit_counts_six_drawn_numbers_but_five_on_540_ticket():
    from scripts.analysis import audit_output
    from loto_enterprise.core.lotteries import require_lottery
    from loto_enterprise.core.walk_forward_adapter import WalkForwardResult

    lot = require_lottery("5/40")
    source = pd.DataFrame({f"n{i}": [i] for i in range(1, 7)})
    flat = [WalkForwardResult(
        draw_index=0, draw_date=None, variant=list(ticket), hits=5,
        hits_union=6, wheel_coverage=100.0,
    ) for ticket in combinations(range(1, 7), 5)]
    per = audit_output.audit_cached_hits(flat, source, lot, 3, 3)
    assert per[0]["best_ticket"] == 5 and per[0]["pool"] == 6


def test_output_audit_does_not_confuse_conditional_cover_with_played_pool():
    from scripts.analysis import audit_output
    from loto_enterprise.core.lotteries import require_lottery
    from loto_enterprise.core.walk_forward_adapter import WalkForwardResult

    lot = require_lottery("5/40")
    source = pd.DataFrame({f"n{i}": [i] for i in range(1, 7)})
    flat = [WalkForwardResult(
        draw_index=0, draw_date=None, variant=[1, 2, 3, 4, 5], hits=5,
        hits_union=6, wheel_coverage=100.0,
    )]
    assert audit_output.audit_cached_hits(flat, source, lot, 3, 4)[0]["pool"] == 6
    with pytest.raises(AssertionError):
        audit_output.audit_cached_hits(flat, source, lot, 3, 3)
