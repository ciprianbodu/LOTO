"""Regresii de validare runtime și afișare din auditul nou al aplicației."""

from __future__ import annotations

import pandas as pd
import pytest

import ui_bench
from loto_enterprise.core import lotteries
from loto_enterprise.core.backtesting import _joker_hit


@pytest.mark.parametrize("actual", [1.5, 20.9, float("inf"), float("-inf"), float("nan"), 0, 21, "1.5", "bad"])
def test_joker_hit_rejects_invalid_target(actual):
    frame = pd.DataFrame({"joker": [actual]})
    assert _joker_hit(frame, 0, [[1, 2, 3, 4, 5, 1]]) is None


@pytest.mark.parametrize("predicted", [1.5, 20.9, float("inf"), float("nan"), 0, 21, "bad"])
def test_joker_hit_rejects_invalid_prediction(predicted):
    frame = pd.DataFrame({"joker": [1]})
    assert _joker_hit(frame, 0, [[1, 2, 3, 4, 5, predicted]]) is None


@pytest.mark.parametrize("actual,predicted,expected", [(1, 1, True), (20, 20, True), (1.0, 1, True), ("20", 20, True), (1, 20, False)])
def test_joker_hit_keeps_valid_binary_result(actual, predicted, expected):
    assert _joker_hit(pd.DataFrame({"joker": [actual]}), 0, [[1, 2, 3, 4, 5, predicted]]) is expected


def _latest_fixture(monkeypatch, game, frame):
    spec = lotteries.GAMES_BY_ID[game]
    monkeypatch.setattr(ui_bench, "STATE", {"datasets": [("history.csv", frame)]})
    monkeypatch.setattr(ui_bench, "_result_source", lambda name: frame, raising=False)
    monkeypatch.setattr(ui_bench, "_game_spec_for", lambda key: spec, raising=False)
    return "history.csv"


@pytest.mark.parametrize("game", ["6/49", "5/40", "joker", "at_lotto", "eu_euromillions"])
def test_latest_draw_uses_chronology_and_registered_geometry(monkeypatch, game):
    spec = lotteries.GAMES_BY_ID[game]
    frame = pd.DataFrame({"date": ["27-09-2026", "24-09-2026"], **{f"n{i}": [i, i + 6] for i in range(1, spec.draw_n + 1)}})
    if game == "joker":
        frame["joker"] = [20, 7]
    fname = _latest_fixture(monkeypatch, game, frame)
    assert ui_bench._last_csv_draw(fname) == ("27-09-2026", list(range(1, spec.draw_n + 1)), 20 if game == "joker" else None)
    assert ui_bench._csv_last_date(frame) == "27-09-2026"


def test_latest_draw_uses_shared_header_normalization(monkeypatch):
    frame = pd.DataFrame({" DATA ": ["24-09-2026", "27-09-2026"], **{f" N{i} ": [i + 6, i] for i in range(1, 7)}})
    fname = _latest_fixture(monkeypatch, "6/49", frame)
    assert ui_bench._last_csv_draw(fname) == ("27-09-2026", [1, 2, 3, 4, 5, 6], None)
    assert ui_bench._csv_last_date(frame) == "27-09-2026"


def test_latest_draw_filters_invalid_rows_like_engine(monkeypatch):
    frame = pd.DataFrame({"date": ["24-09-2026", "27-09-2026"], **{f"n{i}": [i, i] for i in range(1, 7)}})
    frame.loc[1, "n6"] = 5
    fname = _latest_fixture(monkeypatch, "6/49", frame)
    assert ui_bench._last_csv_draw(fname) == ("24-09-2026", [1, 2, 3, 4, 5, 6], None)


@pytest.mark.parametrize("value", [1.5, 20.9, float("inf"), "bad"])
def test_latest_joker_does_not_invent_integer_hit(monkeypatch, value):
    frame = pd.DataFrame({"date": ["27-09-2026"], **{f"n{i}": [i] for i in range(1, 6)}, "joker": [value]})
    fname = _latest_fixture(monkeypatch, "joker", frame)
    assert ui_bench._last_csv_draw(fname) == ("27-09-2026", [1, 2, 3, 4, 5], None)


def test_latest_draw_without_dates_preserves_caller_row_order(monkeypatch):
    frame = pd.DataFrame({f"n{i}": [i, i + 6] for i in range(1, 7)})
    fname = _latest_fixture(monkeypatch, "6/49", frame)
    assert ui_bench._last_csv_draw(fname) == ("", [7, 8, 9, 10, 11, 12], None)
    assert ui_bench._csv_last_date(frame) == ""


def test_latest_draw_invalid_dates_are_not_reported_as_latest(monkeypatch):
    frame = pd.DataFrame({"date": ["bad"], **{f"n{i}": [i] for i in range(1, 7)}})
    fname = _latest_fixture(monkeypatch, "6/49", frame)
    assert ui_bench._last_csv_draw(fname) is None
    assert ui_bench._csv_last_date(frame) == ""



def _leaderboard_frame():
    rows = []
    for pct, n in ((10, 100), (30, 300), (60, 600), (100, 1000)):
        for method, rate in (("random", 0.085), ("frequency", 0.11), ("markov_pairs", 0.10)):
            rows.append(dict(game="joker_urna1", method=method, percentile=pct, n_eval=n, n_test=n, blocks=n, runtime_sec=0.01, is_random=False, failed=False, k11=1.5, rate_3plus_k11=rate, rate_4plus_k11=0.01, tiebreak_k11=0.1))
    return pd.DataFrame(rows)


def test_leaderboard_metrics_ignore_method_specific_extra_window(monkeypatch):
    from loto_enterprise.benchmark import decision
    from scripts.analysis.audit_output import capture_ui

    monkeypatch.setattr(decision, "BENCH_HIT_TARGET", 3)
    monkeypatch.setattr(ui_bench, "_BENCH_FOLDS_CACHE", {"signature": None})
    frame = _leaderboard_frame()
    with capture_ui() as before:
        ui_bench._render_bench_leaderboard_slice(frame, "joker_urna1", 11, "Joker", 20)
    extra = {**frame.iloc[1].to_dict(), "percentile": 25, "n_eval": 10000, "n_test": 10000, "blocks": 10000, "k11": 5.0, "rate_3plus_k11": 1.0}
    with capture_ui() as after:
        ui_bench._render_bench_leaderboard_slice(pd.concat([frame, pd.DataFrame([extra])], ignore_index=True), "joker_urna1", 11, "Joker", 20)
    assert before.ranking() == after.ranking()
    assert before.text() == after.text()
    assert "brut 3+: 11.00%" in after.text()


def test_leaderboard_method_with_only_extra_window_is_visible_and_excluded(monkeypatch):
    from loto_enterprise.benchmark import decision
    from scripts.analysis.audit_output import capture_ui

    monkeypatch.setattr(decision, "BENCH_HIT_TARGET", 3)
    monkeypatch.setattr(ui_bench, "_BENCH_FOLDS_CACHE", {"signature": None})
    frame = _leaderboard_frame()
    extra = {**frame.iloc[1].to_dict(), "method": "hawkes_cross", "percentile": 25, "rate_3plus_k11": 1.0}
    with capture_ui() as capture:
        ui_bench._render_bench_leaderboard_slice(pd.concat([frame, pd.DataFrame([extra])], ignore_index=True), "joker_urna1", 11, "Joker", 20)
    assert "hawkes_cross" in capture.text()
    assert "hawkes_cross" not in capture.ranking()
    assert "date indisponibile" in capture.text()
    assert "ferestre lipsă: 10%, 30%, 60%, 100%" in capture.text()



def test_latest_draw_does_not_replace_missing_snapshot_with_live_csv(monkeypatch):
    frame = pd.DataFrame({f"n{i}": [i] for i in range(1, 7)})
    fname = _latest_fixture(monkeypatch, "6/49", frame)
    monkeypatch.setattr(ui_bench, "_result_source", lambda name: None)
    assert ui_bench._last_csv_draw(fname) is None



def test_latest_draw_snapshot_keeps_game_identity_after_live_rebinding(monkeypatch):
    frame = pd.DataFrame(
        {"date": ["27-09-2026"], **{f"n{i}": [i] for i in range(1, 6)}, "joker": [20]}
    )
    frame.attrs.update(game_id="joker", country="RO")
    # Fișierul live este legat de 6/49; snapshot-ul este rezultatul Joker.
    fname = _latest_fixture(monkeypatch, "6/49", frame)
    assert ui_bench._last_csv_draw(fname) == ("27-09-2026", [1, 2, 3, 4, 5], 20)


def test_latest_draw_snapshot_uses_foreign_number_universe(monkeypatch):
    frame = pd.DataFrame({f"n{i}": [i] for i in range(1, 7)})
    frame.loc[0, "n6"] = 49
    frame.attrs.update(game_id="at_lotto", country="AT")
    fname = _latest_fixture(monkeypatch, "6/49", frame)
    assert ui_bench._last_csv_draw(fname) is None
