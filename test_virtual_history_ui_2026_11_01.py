"""Interfața analizei virtuale este separată de rezultatele cu bilete."""
import json
from datetime import date, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

import app_nicegui as app
from loto_enterprise.core import walk_forward_adapter as wf
from scripts.analysis.audit_output import capture_ui
from test_bench_status_ui import sidebar_context, _finish_ui_updates


class InlineThread:
    def __init__(self, target, **_kwargs): self.target = target
    def start(self): self.target()


@pytest.fixture
def virtual_setup(monkeypatch):
    frame = pd.DataFrame({"date": ["29-10-2026"], **{f"n{i}": [i] for i in range(1, 7)}})
    tasks = []
    for game in ("6/49", "joker", "5/40"):
        task = {"game_label": game, "pool_size": 30, "guarantee": 6, "max_variants": 0,
                "wheel_condition": 0, "recent_penalty_draws": 0,
                "recent_penalty_factor": 0.35, "restrict_base_min": 0,
                "restrict_base_max": 0, "max_consecutive_run": 2, "lookback": 0}
        tasks.append({"fname": f"{game}.csv", "df_json": frame.to_json(orient="split"), "tasks": [task]})
    monkeypatch.setattr(app, "_build_config_json", lambda: json.dumps({"datasets": tasks}))
    monkeypatch.setattr(app, "_bench_running", lambda: False)
    monkeypatch.setattr(app.threading, "Thread", InlineThread)
    # Nu porni I/O oficial în firul executat inline de acest fixture.
    import ui_prizes
    monkeypatch.setattr(ui_prizes.PoolPrizeBinding, "request", lambda *_a, **_kw: None)
    monkeypatch.setattr(app.results_panel, "refresh", lambda: None)
    monkeypatch.setattr(app.ui, "notify", lambda *_a, **_kw: None)
    for key, value in {"virtual_wf_running": False, "virtual_wf_depth": 100,
                       "wf_running": False, "active_job_id": None,
                       "bench_was_running": False, "bench_cancelled": False,
                       "virtual_wf_results": [], "retro": {"old": ["actual"]}}.items():
        monkeypatch.setitem(app.STATE, key, value)
    return tasks


def test_virtual_action_snapshots_three_games_and_has_no_completion_side_effects(virtual_setup, monkeypatch):
    calls = []
    monkeypatch.setattr(app, "_finalize_pipeline", lambda: pytest.fail("shutdown was invoked"))
    monkeypatch.setattr(app, "_maybe_send_results_email", lambda: pytest.fail("mail was invoked"))
    def run(**kw):
        calls.append(kw)
        row = SimpleNamespace(draw_index=7, draw_date="2026-10-29", variant=[1, 2, 3, 4, 5, 6],
            hits=4, hits_union=4, variant_count=593775, virtual_full_system=True,
            ticket_context={"actual": [1, 2, 3, 4, 40, 49], "hard_core": list(range(1, 31))})
        return [row], {"virtual_full_system": True, "n_test_draws": 1, "n_expected": 5, "partial": True}
    monkeypatch.setattr(wf, "run_honest_walk_forward", run)
    app._start_virtual_history()
    assert [call["guarantee"] for call in calls] == [6, 5, 5]
    assert all(call["pool_size"] == 30 and call["backtest_depth_percent"] == 100 for call in calls)
    assert all(call["virtual_full_system"] and call["max_variants"] == 0 for call in calls)
    assert all(call["max_consecutive_run"] == 2 for call in calls)
    assert app.STATE["retro"] == {"old": ["actual"]}
    assert len(app.STATE["virtual_wf_results"]) == 3
    assert not app.STATE["virtual_wf_running"]
    with capture_ui() as view:
        app._render_virtual_history()
    text = view.text()
    assert "nu stabilește că au fost jucate" in text
    assert "construit după extragerea istorică" in text
    assert "fereastră cerută 100%" in text
    tables = [n for n in view.walk() if n["kind"] == "table"]
    details = [n for n in tables if any(c["name"] == "example" for c in n["kwargs"]["columns"])]
    summaries = [n for n in tables if any(c["name"] == "threshold" for c in n["kwargs"]["columns"])]
    assert len(details) == len(summaries) == 3
    assert "Combinații posibile calculate matematic" in str(details[0]["kwargs"]["columns"])
    assert "Exemplu construit după extragere" in str(details[0]["kwargs"]["columns"])
    assert details[0]["kwargs"]["row_key"] == "draw_index"
    assert summaries[0]["kwargs"]["rows"][0] == {"threshold": "4+", "pool": "1 (100.00%)", "ticket": "1 (100.00%)"}
    assert summaries[2]["kwargs"]["rows"][-1]["threshold"] == "6+"
    assert summaries[2]["kwargs"]["rows"][-1]["ticket"] == "—"


def test_virtual_action_refuses_budgeted_or_incomplete_system(virtual_setup, monkeypatch):
    virtual_setup[0]["tasks"][0]["max_variants"] = 100
    calls = []
    monkeypatch.setattr(wf, "run_honest_walk_forward", lambda **kw: calls.append(kw))
    app._start_virtual_history()
    assert not calls and not app.STATE["virtual_wf_running"]


def test_virtual_and_bench_never_start_over_one_another(virtual_setup, monkeypatch):
    calls = []
    monkeypatch.setattr(wf, "run_honest_walk_forward", lambda **kw: calls.append(kw))
    monkeypatch.setattr(app, "_bench_running", lambda: True)
    app._start_virtual_history()
    assert not calls and not app.STATE["virtual_wf_running"]
    monkeypatch.setattr(app, "_bench_running", lambda: False)
    monkeypatch.setitem(app.STATE, "virtual_wf_running", True)
    launched = []
    monkeypatch.setattr(app.subprocess, "Popen", lambda *_a, **_kw: launched.append(True))
    app._launch_bench([], "Re-Bench")
    assert not launched


def test_virtual_waits_for_unprocessed_bench_completion(virtual_setup, monkeypatch):
    calls = []
    monkeypatch.setattr(wf, "run_honest_walk_forward", lambda **kw: calls.append(kw))
    monkeypatch.setitem(app.STATE, "bench_was_running", True)
    monkeypatch.setitem(app.STATE, "bench_cancelled", False)
    app._start_virtual_history()
    assert not calls and not app.STATE["virtual_wf_running"]


def test_external_decision_change_between_games_stops_without_mixing(virtual_setup, monkeypatch, tmp_path):
    from loto_enterprise.core import method_selector

    path = tmp_path / "best_methods.json"
    path.write_text("old", encoding="utf-8")
    monkeypatch.setattr(method_selector, "decision_path_for", lambda _country: path)
    original = app._virtual_decision_fingerprint
    reads = []
    def fingerprint(countries):
        reads.append(True)
        if len(reads) == 4:  # înaintea jocului al doilea
            path.write_text("new", encoding="utf-8")
        return original(countries)
    monkeypatch.setattr(app, "_virtual_decision_fingerprint", fingerprint)
    calls = []
    def run(**kw):
        calls.append(kw)
        row = SimpleNamespace(virtual_full_system=True, draw_index=1)
        return [row], {"virtual_full_system": True, "n_test_draws": 1, "n_expected": 1}
    monkeypatch.setattr(wf, "run_honest_walk_forward", run)
    app._start_virtual_history()
    assert len(calls) == 1 and len(app.STATE["virtual_wf_results"]) == 1
    assert "decizia de bench sau ținta s-a schimbat" in app.STATE["virtual_wf_status"]


def test_external_decision_change_during_game_skips_cache_and_result(virtual_setup, monkeypatch, tmp_path):
    from loto_enterprise.core import method_selector

    path = tmp_path / "best_methods.json"
    path.write_text("old", encoding="utf-8")
    monkeypatch.setattr(method_selector, "decision_path_for", lambda _country: path)
    def run(**kw):
        path.write_text("new", encoding="utf-8")
        assert kw["should_skip_cache_write"]()
        assert kw["should_cancel"]()
        return [SimpleNamespace(virtual_full_system=True)], {"virtual_full_system": True}
    monkeypatch.setattr(wf, "run_honest_walk_forward", run)
    app._start_virtual_history()
    assert not app.STATE["virtual_wf_results"]
    assert "decizia de bench sau ținta s-a schimbat" in app.STATE["virtual_wf_status"]


def test_target_change_signals_virtual_stop(sidebar_context, monkeypatch):
    from nicegui import binding, ui
    client, settings, state = sidebar_context
    settings["bench_hit_target"] = 3
    state["virtual_wf_running"] = True
    app._VIRTUAL_WF_DECISION_CHANGED.clear()
    monkeypatch.setattr(app, "_bench_running", lambda: False)
    monkeypatch.setattr(app, "_rebuild_decision_for_target", lambda *_a, **_kw: None)
    app.main_page()
    selector = next(e for e in client.elements.values() if isinstance(e, ui.select)
                    and e._props.get("label") == "🎯 Țintă Optimizare / Bench")
    selector.value = 4
    binding._refresh_step()
    _finish_ui_updates()
    assert app._VIRTUAL_WF_DECISION_CHANGED.is_set()


def test_virtual_ui_offers_explicit_depth_and_does_not_autostart(virtual_setup, monkeypatch):
    import ui_hits
    monkeypatch.setitem(app.STATE, "virtual_wf_depth", 30)
    starts = []
    monkeypatch.setattr(app, "_start_virtual_history", lambda: starts.append(True))
    with capture_ui() as view:
        ui_hits._render_analysis_menu([])
    sections = [n for n in view.walk() if n["kind"] == "expansion" and n["args"][0] == "📊 Analiză & Clasament"]
    assert len(sections) == 1
    selectors = [n for n in sections[0]["children"] if n["kind"] == "select" and n["kwargs"].get("label") == "Adâncime istoric virtual"]
    assert len(selectors) == 1 and selectors[0]["kwargs"]["value"] == 30
    assert set(selectors[0]["args"][0]) == {30, 100}
    buttons = [n for n in sections[0]["children"] if n["kind"] == "button" and "Calculează istoric virtual" in n["args"][0]]
    assert not buttons
    assert not starts


@pytest.mark.parametrize("recovered", [False, True])
def test_virtual_results_and_action_are_inside_one_analysis_section(virtual_setup, monkeypatch, recovered):
    import ui_hits

    row = SimpleNamespace(draw_index=1, draw_date="2026-10-29", variant=[1, 2, 3, 4, 5, 6],
        hits=4, hits_union=4, variant_count=593775, virtual_full_system=True,
        ticket_context={"actual": [1, 2, 3, 4, 40, 49], "hard_core": list(range(1, 31))})
    record = {"name": "Loto 6/49", "game": "6/49", "pool": 30, "guarantee": 6,
        "depth": 30, "flat": [row], "meta": {"n_test_draws": 1, "n_expected": 1}}
    monkeypatch.setitem(app.STATE, "virtual_wf_results", [record])
    monkeypatch.setitem(app.STATE, "virtual_wf_status", "Analiză virtuală încheiată.")
    monkeypatch.setitem(app.STATE, "results_recovered", "job #28" if recovered else None)
    monkeypatch.setattr(ui_hits, "_render_game_analysis", lambda *_a, **_kw: None)
    bundle = [("history.csv", {"6/49": {}})] if recovered else []
    with capture_ui() as view:
        ui_hits._render_analysis_menu(bundle)
    sections = [n for n in view.walk() if n["kind"] == "expansion" and n["args"][0] == "📊 Analiză & Clasament"]
    assert len(sections) == 1
    def descendants(node):
        for child in node["children"]:
            yield child
            yield from descendants(child)
    inside = list(descendants(sections[0]))
    assert not any(n["kind"] == "button" and "Calculează istoric virtual" in n["args"][0] for n in inside)
    assert sum(n["kind"] == "expansion" and "Istoric hits — sistem complet virtual" in n["args"][0] for n in inside) == 1
    assert any(n["kind"] == "table" and n["kwargs"].get("row_key") == "draw_index" for n in inside)


def test_real_results_panel_exposes_analysis_without_a_generated_job(sidebar_context):
    from nicegui import ui
    import ui_results

    client, _settings, state = sidebar_context
    assert state["results"] is None
    with client:
        ui_results.results_panel()
    buttons = [e for e in client.elements.values() if isinstance(e, ui.button)
               and "Calculează istoric virtual" in e.text]
    assert not buttons


def test_analysis_expansion_keeps_user_choice_after_refresh(sidebar_context):
    import asyncio
    from nicegui import core, ui
    import ui_results

    client, _settings, state = sidebar_context
    state.pop("analysis_expanded", None)
    state["virtual_wf_status"] = "Analiză virtuală încheiată."
    with client:
        ui_results.results_panel()
    def section():
        return next(e for e in client.elements.values() if isinstance(e, ui.expansion)
                    and e._props.get("label") == "📊 Analiză & Clasament")
    original = section()
    assert original.value is True
    original.value = False
    assert state["analysis_expanded"] is False
    ui_results.results_panel.refresh()
    core.loop.run_until_complete(asyncio.sleep(0))
    assert section().value is False
    section().value = True
    assert state["analysis_expanded"] is True
    ui_results.results_panel.refresh()
    core.loop.run_until_complete(asyncio.sleep(0))
    assert section().value is True


@pytest.mark.parametrize("game,pick", [("6/49", 6), ("joker", 5), ("5/40", 5)])
def test_virtual_ui_runs_real_backend_without_materializing_tickets(
    virtual_setup, monkeypatch, tmp_path, game, pick
):
    import math
    import loto_enterprise.core.backtesting as backtesting
    from loto_engine import LotoEngine
    dataset = next(d for d in virtual_setup if d["tasks"][0]["game_label"] == game)
    draw_n = 5 if game == "joker" else 6
    maximum = {"6/49": 49, "5/40": 40, "joker": 45}[game]
    rows = [[(i * 7 + j * 5) % maximum + 1 for j in range(draw_n)] for i in range(50)]
    frame = pd.DataFrame(rows, columns=[f"n{i}" for i in range(1, draw_n + 1)])
    frame["date"] = [(date(2020, 1, 1) + timedelta(days=i)).strftime("%d-%m-%Y") for i in range(50)]
    if game == "joker": frame["joker"] = [1 + i % 20 for i in range(50)]
    dataset["df_json"] = frame.to_json(orient="split")
    virtual_setup[:] = [dataset]
    monkeypatch.setitem(app.STATE, "virtual_wf_depth", 30)
    monkeypatch.setattr(wf, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(backtesting, "_WF_PROBE_STEPS", 1)
    monkeypatch.setattr(backtesting, "_WF_SERIAL_MAX_MS", 1e9)
    monkeypatch.setattr(LotoEngine, "generate_predictions", lambda *_a, **_k: pytest.fail("bilete materializate"))
    app._start_virtual_history()
    record, = app.STATE["virtual_wf_results"]
    assert "error" not in record and record["meta"]["n_test_draws"] > 0
    assert all(row.virtual_full_system and row.variant_count == math.comb(30, pick) for row in record["flat"])
    assert app.STATE["retro"] == {"old": ["actual"]}
