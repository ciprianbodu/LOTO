"""Regresii ale outputului: ordine, geometrie, baseline și hituri pe bilet."""
from types import SimpleNamespace

import pandas as pd

import app_nicegui as app_ui
from loto_enterprise.benchmark import decision
from scripts.analysis.audit_output import capture_ui


def _folds():
    rows = []
    for pct, n in ((10, 100), (30, 300), (60, 600), (100, 1000)):
        for method, rate, ties in (("frequency", 0.10, 0.1),
                                   ("649_mom_15_60", 0.15, 0.7),
                                   ("random", 0.085, 0.9)):
            rows.append({"game": "joker_urna1", "method": method, "percentile": pct,
                         "n_test": n, "n_eval": n, "is_random": False, "failed": False,
                         "k11": 1.3, "avg_hits_topk": 0.6, "rate_3plus_k11": rate,
                         "rate_4plus_k11": rate / 10, "tiebreak_k11": ties,
                         "runtime_sec": 0.01})
    return pd.DataFrame(rows)


def test_rendered_ranking_obeys_structural_gate_without_disqualifying_baseline(monkeypatch):
    monkeypatch.setattr(decision, "BENCH_HIT_TARGET", 3)
    monkeypatch.setattr(app_ui, "_BENCH_FOLDS_CACHE", {"signature": None, "df": None})
    with capture_ui() as ui:
        app_ui._render_bench_leaderboard_slice(_folds(), "joker_urna1", 11, "Joker", 20)
    assert ui.ranking()[0] == "frequency"
    assert "649_mom_15_60" not in ui.ranking()
    assert "70.0%" in ui.text()
    assert "1 excluse structural" in ui.text()  # random rămâne doar reper


def test_no_eligible_method_still_renders_exclusion_reasons(monkeypatch):
    monkeypatch.setattr(app_ui, "_BENCH_FOLDS_CACHE", {"signature": None, "df": None})
    df = _folds().query("method != 'frequency'")
    with capture_ui() as ui:
        app_ui._render_bench_leaderboard_slice(df, "joker_urna1", 11, "Joker", 20)
    assert "Nicio metodă eligibilă" in ui.text()
    assert "649_mom_15_60" in ui.text()
    assert not ui.ranking()


def test_leaderboard_uses_result_pool_after_setting_changes(monkeypatch):
    calls = []
    monkeypatch.setitem(app_ui.SETTINGS, "pool_size_val", 16)
    monkeypatch.setattr(app_ui, "_read_bench_folds_cached", lambda _: _folds())
    monkeypatch.setattr(app_ui, "_render_bench_leaderboard_slice",
                        lambda df, game, pool, label, **kw: calls.append((game, pool)))
    app_ui._render_bench_leaderboard("joker", pool_size=11)
    assert calls == [("joker_urna1", 11), ("joker_urna2", 1)]


def test_last_generation_badge_does_not_leak_to_another_pool(monkeypatch):
    info = {"method": "frequency", "pool_hint": 10}
    monkeypatch.setitem(app_ui.STATE, "results", (
        [("joker.csv", {"joker": {"pool_size": 10, "audit": {
            "bench_winner": {"joker_urna1": info}}}})], 0,
    ))
    assert app_ui._last_generation_bench_info("joker_urna1", 10) == info
    assert app_ui._last_generation_bench_info("joker_urna1", 11) == {}


def test_wf_output_distinguishes_pool_hits_from_incomplete_tickets():
    flat = [SimpleNamespace(draw_index=1, draw_date="01-09-2026", hits_union=4,
                            hits=h, wheel_coverage=25.0) for h in (1, 2)]
    with capture_ui() as ui:
        app_ui._render_hits_4plus(flat, "6/49", {"pool_size": 11})
    table = next(n for n in ui.walk() if n["kind"] == "table")
    pool, tickets = table["kwargs"]["rows"]
    assert pool["p3"] == pool["p4"] == "1 (100.00%)"
    assert tickets["p3"] == tickets["p4"] == "0 (0.00%)"
    assert tickets["rnd"] == "—"


def test_report_explains_transforms_without_mutating_payload(monkeypatch):
    audit = {"timesfm_predictions": {1: 0.5}, "pure_bench_mode": True,
             "recent_penalty": {"draws": 3, "factor": 0.5, "penalized": {1: 2}}}
    data = {"pool_size": 6, "hard_core": [1, 2, 3, 4, 5, 6], "variants": [],
            "guarantee": 4, "context": {"coverage_pct": 25.0}, "audit": audit}
    monkeypatch.setitem(app_ui.STATE, "results", ([("x.csv", {"6/49": data})], 0))
    monkeypatch.setitem(app_ui.STATE, "retro", {})
    text = app_ui._build_report()
    assert "25.00%" in text
    assert "fără aceste ajustări" in text
    assert "ranking_scores_top25" in text
    assert "timesfm_predictions" not in text
    assert "timesfm_predictions" in audit
    assert audit["pure_bench_mode"] is True
