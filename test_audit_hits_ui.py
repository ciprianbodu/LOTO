"""Regresii UI: pragurile pe joc, comparația după antrenare și 5+ pe bilet."""

from __future__ import annotations

from datetime import date
from math import comb
from types import SimpleNamespace

import pandas as pd
import pytest

import app_nicegui as app
import ui_bench
import ui_hits
import ui_results
from loto_enterprise.benchmark import decision
from scripts.analysis.audit_output import capture_ui


@pytest.mark.parametrize("target", [3, 4])
def test_bench_rule_respects_each_game_without_changing_target(monkeypatch, target):
    monkeypatch.setitem(app.SETTINGS, "bench_hit_target", target)
    text = app._bench_target_rule_text("RO")
    assert f"6/49 și Joker Urna 1: {target}+" in text
    assert "Loto 5/40: minimum 4+" in text
    assert "Joker Urna 2: top-1, potrivire exactă" in text
    assert app.SETTINGS["bench_hit_target"] == target
    assert f"Lotto 6 aus 45: {target}+" in app._bench_target_rule_text("AT")


@pytest.mark.parametrize("picked,matched", [(7, True), (8, False)])
def test_joker_production_comparison_is_explicitly_after_training(
    monkeypatch, picked, matched
):
    monkeypatch.setattr(ui_bench, "_last_csv_draw", lambda _: ("01-10-2026", [1, 2, 3, 4, 5], 7))
    with capture_ui() as ui:
        ui_bench._render_last_csv_draw("joker.csv", joker_pick=[picked])
    text = ui.text()
    assert f"extras 7, ales {picked}" in text
    assert "după antrenarea pe această extragere; nu rezultat predictiv" in text
    assert ("Fără potrivire" not in text) == matched
    assert "hit pe" not in text
    assert "prezis" not in text


def _folds(game, pool, target):
    rows = []
    for pct in (10, 30, 60, 100):
        for method, rate in (("random", 0.05), ("frequency", 0.20)):
            rows.append({
                "game": game, "method": method, "percentile": pct,
                "n_test": 100, "n_eval": 100, "is_random": False, "failed": False,
                f"k{pool}": 1.3, "avg_hits_topk": 0.6,
                f"rate_{target}plus_k{pool}": rate, f"tiebreak_k{pool}": 0.1,
                "runtime_sec": 0.01,
            })
    return pd.DataFrame(rows)


@pytest.mark.parametrize("game,pool,target", [("joker_urna1", 11, 3), ("joker_urna2", 1, 1)])
def test_rendered_wilson_is_a_heuristic_ranking_score(monkeypatch, game, pool, target):
    monkeypatch.setattr(decision, "BENCH_HIT_TARGET", 3)
    monkeypatch.setitem(app.SETTINGS, "bench_hit_target", 3)
    monkeypatch.setattr(ui_bench, "_LB_ROWS_MEMO", {})
    monkeypatch.setattr(ui_bench, "_BENCH_FOLDS_CACHE", {"signature": None, "df": None})
    with capture_ui() as ui:
        ui_bench._render_bench_leaderboard_slice(_folds(game, pool, target), game, pool, "Joker")
    text = ui.text()
    assert "Wilson" in text
    assert "z=1 ca scor de clasare" in text
    assert "nu este un interval de încredere de 95%" in text
    assert "Calificarea este euristică" in text


def _at_least_five(max_num, draw_n, selected_n):
    return sum(
        comb(selected_n, h) * comb(max_num - selected_n, draw_n - h)
        for h in range(5, min(draw_n, selected_n) + 1)
    ) / comb(max_num, draw_n)


@pytest.mark.parametrize("game,draw_n,max_num,pick", [
    ("6/49", 6, 49, 6), ("5/40", 6, 40, 5), ("joker", 5, 45, 5),
    ("at_lotto", 6, 45, 6), ("eu_euromillions", 5, 50, 5),
])
def test_five_plus_odds_are_exact_and_keep_pool_separate_from_ticket(game, draw_n, max_num, pick):
    ticket = list(range(1, pick + 1)) + ([7] if game == "joker" else [])
    lines = ui_results._wheel_probability_lines(game, {"hard_core": list(range(1, 9)), "variants": [ticket]})
    expected_pool = 100 * _at_least_five(max_num, draw_n, 8)
    expected_ticket = 100 * _at_least_five(max_num, draw_n, pick)
    assert expected_pool > expected_ticket
    assert any(
        f"5+ numere: în pool {expected_pool:.3f}%; pe cel puțin o variantă {expected_ticket:.3f}%" in line
        for line in lines
    )
    assert "Garanția 4 nu asigură 5 pe un bilet; compară probabilitățile separate." in lines


def _wf_entries():
    flat = []
    # Mai multe variante pe extragere: trei pool-uri cu 5+, dar un singur bilet cu 5+.
    for index, day, pool, best in ((1, 1, 5, 4), (2, 5, 5, 5), (3, 8, 5, 4), (4, 15, 4, 4), (5, 25, 3, 3)):
        for hits in (best - 1, best):
            flat.append(SimpleNamespace(
                draw_index=index, draw_date=f"{day:02d}-09-2026", hits_union=pool,
                hits=hits, wheel_coverage=100.0,
            ))
    return flat


def test_five_plus_summary_counts_draws_and_real_ticket_hits():
    text = ui_results._wf_summary(_wf_entries())
    assert "5 extrageri" in text
    assert "pool 3+/4+: 5/4; bilet 3+/4+: 5/4" in text
    assert "pool 5+: 3; bilet 5+: 1" in text


def test_wf_table_and_ticket_gaps_use_each_threshold_separately(monkeypatch):
    gap_rows = ui_hits._hit_gap_rows
    monkeypatch.setattr(ui_hits, "_hit_gap_rows", lambda items: gap_rows(items, today=date(2026, 10, 1)))
    with capture_ui() as ui:
        ui_hits._render_hits_4plus(_wf_entries(), "6/49", {"pool_size": 16})
    summary = next(node for node in ui.walk() if node["kind"] == "table")
    pool, ticket = summary["kwargs"]["rows"]
    assert pool["p5"] == "3 (60.00%)"
    assert ticket["p5"] == "1 (20.00%)"
    assert ticket["rnd"] == "—"
    # Random pool 16, 6/49, 5+ rămâne probabilitatea exactă hipergeometrică.
    assert pool["rnd"].endswith(f"{100 * _at_least_five(49, 6, 16):.3f}%")
    details = next(node for node in ui.walk() if node["kind"] == "expansion" and "Bilete WF" in node["args"][0])
    assert details["kwargs"]["value"] is False
    tables = [node for node in details["children"] if node["kind"] == "table"]
    four = tables[0]["kwargs"]["rows"]
    five = tables[1]["kwargs"]["rows"]
    assert [row["draw"] for row in four] == ["15-09-2026", "08-09-2026", "05-09-2026", "01-09-2026"]
    assert [row["gap"] for row in four] == ["acum 16 zile", "7 zile", "3 zile", "4 zile"]
    assert five == [{"draw": "05-09-2026", "hits": "5 numere", "gap": "acum 26 zile"}]


def test_five_plus_probabilities_and_counts_are_shared_with_saved_report(monkeypatch):
    data = {
        "hard_core": list(range(1, 9)), "pool_size": 8,
        "variants": [list(range(1, 6))], "guarantee": 4,
        "context": {"coverage_pct": 100.0},
    }
    monkeypatch.setitem(app.STATE, "results", ([("test.csv", {"5/40": data})], {}))
    monkeypatch.setitem(app.STATE, "retro", {"test.csv_5/40": _wf_entries()})
    with capture_ui() as ui:
        ui_results._render_cost("5/40", data)
    report = ui_results._build_report()
    five_line = next(line for line in ui_results._wheel_probability_lines("5/40", data) if "5+ numere" in line)
    assert five_line in ui.text()
    assert five_line in report
    assert "pool 5+: 3; bilet 5+: 1" in report
