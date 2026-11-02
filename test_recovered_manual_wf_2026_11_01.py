"""Validarea unui rezultat recuperat pornește numai la cerere."""
from types import SimpleNamespace

import pandas as pd
import pytest

import app_nicegui as app
import ui_hits
from loto_enterprise.core import walk_forward_adapter as wf
from scripts.analysis.audit_output import capture_ui
from test_bench_status_ui import sidebar_context, _finish_ui_updates


class InlineThread:
    def __init__(self, target, **_kwargs): self.target = target
    def start(self): self.target()


@pytest.fixture
def recovered(monkeypatch):
    frame = pd.DataFrame({"date": ["24-09-2026"], **{f"n{i}": [i] for i in range(1, 7)}})
    data = {"hard_core": list(range(1, 17)), "pool_size": 16, "guarantee": 4, "audit": {}}
    for key, value in {
        "results": ([("history.csv", {"6/49": data})], 1),
        "result_sources": {"history.csv": frame}, "results_recovered": "job #28",
        "retro": {}, "retro_meta": {}, "wf_seq": 0, "wf_running": False,
        "wf_user_cancel": False, "active_job_id": None, "job_start_time": None,
    }.items(): monkeypatch.setitem(app.STATE, key, value)
    notices = []
    monkeypatch.setattr(app.ui, "notify", lambda message, **_kw: notices.append(message))
    monkeypatch.setattr(app, "_refresh_status", lambda: None)
    monkeypatch.setattr(app.results_panel, "refresh", lambda: None)
    monkeypatch.setattr(app, "_save_report_file", lambda: None)
    monkeypatch.setattr(app, "_echo_mismatch", lambda *_: False)
    monkeypatch.setattr(app.threading, "Thread", InlineThread)
    return frame, data, notices


def test_manual_recovered_wf_uses_original_snapshot_without_shutdown(recovered, monkeypatch):
    frame, _data, notices = recovered
    calls, finalized = [], []
    monkeypatch.setattr(app, "_finalize_pipeline", lambda: finalized.append(True))
    def run(**kwargs):
        calls.append(kwargs)
        return [{"draw_index": 1}], {"partial": False, "n_test_draws": 1, "n_expected": 1}
    monkeypatch.setattr(wf, "run_honest_walk_forward", run)
    app._start_recovered_walk_forward()
    assert len(calls) == 1 and calls[0]["df_source"] is frame
    assert app.STATE["retro"]["history.csv_6/49"] == [{"draw_index": 1}]
    assert not finalized
    assert any("Calculez istoricul" in n for n in notices)


def test_new_generation_retains_normal_automatic_finalization(recovered, monkeypatch):
    finalized = []
    monkeypatch.setattr(app, "_finalize_pipeline", lambda: finalized.append(True))
    monkeypatch.setattr(wf, "run_honest_walk_forward", lambda **_kw: (
        [{"draw_index": 1}], {"partial": False, "n_test_draws": 1, "n_expected": 1},
    ))
    app._start_walk_forward()
    assert finalized == [True]


def test_resource_refusal_keeps_exact_explanation_for_display(recovered, monkeypatch):
    detail = "Calculul necesită 4.000.000 grupe; limita este 2.000.000."
    meta = {"large_pool_wf_skipped": True, "skip_reason": "resource_limit", "skip_detail": detail}
    app._store_wf_result("history.csv_6/49", [], meta)
    assert app.STATE["retro_meta"]["history.csv_6/49"]["skip_detail"] == detail
    monkeypatch.setattr(ui_hits, "_render_bench_leaderboard", lambda *_a, **_k: None)
    monkeypatch.setattr(ui_hits, "_render_last_csv_draw", lambda *_a, **_k: None)
    monkeypatch.setattr(ui_hits, "_bench_target", lambda: 4)
    with capture_ui() as view:
        ui_hits._render_game_analysis("history.csv", "6/49", recovered[1])
    assert detail in view.text()


@pytest.mark.parametrize("sources", [None, {}, {"other.csv": object()}])
def test_manual_recovered_wf_rejects_missing_original_snapshot(recovered, monkeypatch, sources):
    _frame, _data, notices = recovered
    monkeypatch.setitem(app.STATE, "result_sources", sources)
    starts = []
    monkeypatch.setattr(app, "_start_walk_forward", lambda **kw: starts.append(kw))
    app._start_recovered_walk_forward()
    assert not starts
    assert any("nu sunt disponibile" in n for n in notices)


@pytest.mark.parametrize("busy", ["wf_running", "active_job_id"])
def test_manual_recovered_wf_does_not_start_over_active_work(recovered, monkeypatch, busy):
    monkeypatch.setitem(app.STATE, busy, True)
    starts = []
    monkeypatch.setattr(app, "_start_walk_forward", lambda **kw: starts.append(kw))
    app._start_recovered_walk_forward()
    assert not starts


def test_recovered_action_is_visible_without_folds_or_cached_history(recovered, monkeypatch, tmp_path):
    _frame, data, _notices = recovered
    monkeypatch.setattr(ui_hits, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(ui_hits, "_render_bench_leaderboard", lambda *_a, **_k: None)
    monkeypatch.setattr(ui_hits, "_render_last_csv_draw", lambda *_a, **_k: None)
    monkeypatch.setattr(ui_hits, "_bench_target", lambda: 4)
    starts = []
    monkeypatch.setattr(ui_hits, "_start_recovered_walk_forward", lambda: starts.append(True))
    with capture_ui() as view:
        ui_hits._render_analysis_menu([("history.csv", {"6/49": data})])
    buttons = [node for node in view.walk() if node["kind"] == "button" and "Calculează Istoric hits" in node["args"][0]]
    assert not buttons and not starts


def test_effective_guarantees_follow_live_input_for_all_three_games(sidebar_context):
    from nicegui import binding, ui

    client, settings, _state = sidebar_context
    settings["guarantee_val"] = 6
    app.main_page()
    output = next(
        element for element in client.elements.values()
        if isinstance(element, ui.label) and element.text.startswith("Garanția cerută pe joc:")
    )
    field = next(
        element for element in client.elements.values()
        if isinstance(element, ui.number)
        and element._props.get("label") == "Garanție minimă (Set Cover)"
    )
    assert "6/49: 6" in output.text
    assert "5/40: 5 (plafonată la 5)" in output.text
    assert "Joker: 5 (plafonată la 5)" in output.text
    for value in (3, 4, 5):
        field.value = value
        binding._refresh_step()
        _finish_ui_updates()
        assert settings["guarantee_val"] == value
        assert f"6/49: {value}" in output.text
        assert f"5/40: {value}" in output.text
        assert f"Joker: {value}" in output.text
        assert "plafonată" not in output.text
