"""Live sidebar text updates without rebuilding the page or touching user state."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from unittest.mock import Mock

import pytest
from nicegui import binding, core, ui
from nicegui.client import Client

import app_nicegui as app
import ui_bench
import ui_hits
import ui_results
import ui_runtime
from loto_enterprise.benchmark import decision, freshness


@pytest.fixture
def sidebar_context(monkeypatch, tmp_path):
    settings = {
        **deepcopy(app.SETTINGS),
        "country_val": "RO",
        "games_val": [],
        "bench_hit_target": 3,
        "autopilot_after_bench": False,
        "restrict_base_enabled_val": False,
    }
    state = {
        **app.STATE,
        "datasets": [],
        "dataset_game": {},
        "active_job_id": None,
        "results": None,
        "wf_running": False,
    }
    for module in (app, ui_runtime, ui_bench, ui_hits, ui_results):
        monkeypatch.setattr(module, "SETTINGS", settings)
        monkeypatch.setattr(module, "STATE", state)
        monkeypatch.setattr(module, "PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(module, "UI_STATE_FILE", tmp_path / "ui_state.json")
    monkeypatch.setattr(decision, "BENCH_HIT_TARGET", 3)
    monkeypatch.setenv("LOTO_BENCH_TARGET", "3")
    monkeypatch.setattr(app, "_save_settings", Mock())
    monkeypatch.setattr(app, "_autoload_histories", Mock(return_value=([], [])))
    monkeypatch.setattr(app, "_load_registry_histories", Mock(return_value=([], [])))
    monkeypatch.setattr(app, "_target_bench_folds", lambda: 0)
    monkeypatch.setattr(app, "_curation_banner_info", lambda: None)
    monkeypatch.setattr(app, "_target_data_ready", lambda: True)
    for name in ("status_panel", "logs_panel", "results_panel"):
        monkeypatch.setattr(app, name, Mock())
    monkeypatch.setattr(app, "_render_base_interval_tables", Mock())
    monkeypatch.setattr(app, "_refresh_status", Mock())
    monkeypatch.setattr(app, "apply_autopilot_and_generate", Mock())
    monkeypatch.setattr(ui, "timer", Mock())
    monkeypatch.setattr(ui.navigate, "reload", Mock())
    loop = asyncio.new_event_loop()
    monkeypatch.setattr(core, "loop", loop)
    client = Client(ui.page("/bench-status-regression"))
    with client:
        yield client, settings, state
    client.delete()
    app._bench_freshness_panel.prune()
    pending = asyncio.all_tasks(loop)
    for task in pending:
        task.cancel()
    if pending:
        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
    loop.close()
    assert not (tmp_path / "ui_state.json").exists()


def _finish_ui_updates():
    # refresh() is fire-and-forget in NiceGUI. Run its real scheduled work.
    core.loop.run_until_complete(asyncio.sleep(0))


def _texts(client):
    return [
        element.text
        for element in client.elements.values()
        if isinstance(element, ui.label)
    ]


def _fresh_summary(rec="use_cache", total=0):
    return {
        "total": total,
        "per": {"loto_6_49": total} if total else {},
        "rec": rec,
        "any_bench": True,
    }


@pytest.mark.parametrize("country", ["RO", "AT"])
def test_actual_sidebar_intro_follows_target_without_reload(
    sidebar_context, monkeypatch, country
):
    client, settings, _state = sidebar_context
    settings["country_val"] = country
    monkeypatch.setattr(app, "_new_draws_summary", lambda: _fresh_summary())
    if country != "RO":
        monkeypatch.setattr(
            app,
            "_country_bench_texts",
            lambda cc: {
                "intro": app._bench_intro_text(cc),
                "eta": "Test ETA",
                "curation": "Test curation",
                "freshness": "Test freshness",
            },
        )
    with ui.expansion("Keep this panel open", value=True) as sentinel:
        ui.label("Existing result content")
    app.main_page()
    introductory = [
        element for element in client.elements.values()
        if isinstance(element, ui.label)
        and "un singur bench" in element.text.lower()
    ]
    assert len(introductory) == 1
    intro = introductory[0]
    old_id = intro.id
    if country == "RO":
        assert "6/49 și Joker Urna 1: 3+" in intro.text
    else:
        assert "Lotto 6 aus 45: 3+" in intro.text
    selector = next(
        element for element in client.elements.values()
        if isinstance(element, ui.select)
        and element._props.get("label") == "🎯 Țintă Optimizare / Bench"
    )
    selector.value = 4
    binding._refresh_step()
    _finish_ui_updates()
    assert settings["bench_hit_target"] == 4
    assert client.elements[old_id] is intro
    if country == "RO":
        assert "6/49 și Joker Urna 1: 4+" in intro.text
        assert "Loto 5/40: minimum 4+" in intro.text
        assert "Joker Urna 2: top-1" in intro.text
    else:
        assert "Lotto 6 aus 45: 4+" in intro.text
    assert "3+" not in intro.text
    assert sentinel.value is True and client.elements[sentinel.id] is sentinel
    assert "Existing result content" in _texts(client)
    ui.navigate.reload.assert_not_called()
    app._autoload_histories.assert_called_once()
    app.results_panel.refresh.assert_not_called()
    app.apply_autopilot_and_generate.assert_not_called()


def test_bench_completion_updates_freshness_with_autopilot_disabled(
    sidebar_context, monkeypatch
):
    client, settings, state = sidebar_context
    current = {"value": _fresh_summary("quick_rebench", 1)}
    monkeypatch.setattr(app, "_new_draws_summary", lambda: current["value"])
    # Data and an idle queue make the Auto-Pilot checkbox the decisive guard.
    state["datasets"] = [("dummy.csv", object())]
    assert settings["autopilot_after_bench"] is False
    with ui.expansion("Unrelated open result", value=True) as sentinel:
        ui.label("Result remains visible")
    app._bench_freshness_panel("RO")
    before = "\n".join(_texts(client))
    assert "Datele noi invalidează cache-ul" in before
    current["value"] = _fresh_summary()
    app._on_bench_finished()
    _finish_ui_updates()
    after = "\n".join(_texts(client))
    assert "Benchmark la zi" in after
    assert "Datele noi invalidează cache-ul" not in after
    assert "Result remains visible" in after
    assert sentinel.value is True and client.elements[sentinel.id] is sentinel
    app.apply_autopilot_and_generate.assert_not_called()
    app.results_panel.refresh.assert_not_called()
    ui.navigate.reload.assert_not_called()


def test_completion_keeps_warning_when_freshness_is_still_stale(
    sidebar_context, monkeypatch
):
    client, _settings, _state = sidebar_context
    monkeypatch.setattr(
        app, "_new_draws_summary", lambda: _fresh_summary("full_rebench", 1)
    )
    app._bench_freshness_panel("RO")
    app._on_bench_finished()
    _finish_ui_updates()
    text = "\n".join(_texts(client))
    assert "Datele noi invalidează cache-ul" in text
    assert "Benchmark la zi" not in text
    app.apply_autopilot_and_generate.assert_not_called()


def test_foreign_freshness_panel_reloads_its_country_context(
    sidebar_context, monkeypatch
):
    client, _settings, _state = sidebar_context
    reports = {"text": "Austria: benchmark neactualizat"}
    calls = []

    def country_texts(country):
        calls.append(country)
        return {"freshness": reports["text"]}

    monkeypatch.setattr(app, "_country_bench_texts", country_texts)
    app._bench_freshness_panel("AT")
    assert reports["text"] in _texts(client)
    reports["text"] = "Austria: date neschimbate"
    app._on_bench_finished()
    _finish_ui_updates()
    assert reports["text"] in _texts(client)
    assert "Austria: benchmark neactualizat" not in _texts(client)
    assert calls == ["AT", "AT"]


def test_unavailable_freshness_cannot_render_a_fresh_banner(
    sidebar_context, monkeypatch
):
    client, _settings, _state = sidebar_context

    def unavailable():
        raise OSError("simulated unavailable history")

    monkeypatch.setattr(freshness, "check_freshness", unavailable)
    assert app._new_draws_summary() is None
    app._bench_freshness_panel("RO")
    app._on_bench_finished()
    _finish_ui_updates()
    assert "Benchmark la zi" not in "\n".join(_texts(client))



def test_target_change_refreshes_notice_even_if_decision_update_fails(
    sidebar_context, monkeypatch, tmp_path
):
    client, settings, _state = sidebar_context
    directory = tmp_path / "bench_results"
    directory.mkdir()
    (directory / "folds.csv").write_text("unused\n", encoding="utf-8")
    update = Mock(side_effect=ValueError("simulated invalid decision"))
    monkeypatch.setattr(decision, "update_best_methods_with_auto_pilot", update)
    monkeypatch.setattr(app, "_new_draws_summary", lambda: _fresh_summary())
    monkeypatch.setattr(
        app, "_target_data_ready", lambda: settings["bench_hit_target"] == 3
    )
    app.main_page()
    assert "Benchmark la zi" in "\n".join(_texts(client))
    selector = next(
        element for element in client.elements.values()
        if isinstance(element, ui.select)
        and element._props.get("label") == "🎯 Țintă Optimizare / Bench"
    )
    selector.value = 4
    binding._refresh_step()
    _finish_ui_updates()
    text = "\n".join(_texts(client))
    update.assert_called_once_with(require_complete=True)
    assert "Lipsesc rezultatele pentru ținta curentă (≥4)" in text
    assert "Benchmark la zi" not in text
    app.results_panel.refresh.assert_not_called()
    ui.navigate.reload.assert_not_called()


# --------------------------------------------------------------------------- #
# Decizia salvată pe altă țintă decât cea selectată (audit 2026-10-08)
# --------------------------------------------------------------------------- #
_TARGET_RATES = {  # (rata 3+, rata 4+) pe k6: 3+ alege frequency, 4+ markov_lag2
    "frequency": (0.05, 0.0),
    "markov_lag2": (0.03, 0.01),
    "random": (0.018, 0.001),
}


def _decision_built_for_3(monkeypatch, root, *, complete=True):
    """best_methods.json scris pe 3+ (bench din consolă fără LOTO_BENCH_TARGET)."""
    import json

    import pandas as pd

    pcts = (10, 30, 60, 100)
    rows = [
        {
            "game": "loto_6_49",
            "method": m,
            "percentile": p,
            "is_random": False,
            "n_test": 1000,
            "n_eval": 1000,
            "k6": 0.7,
            "rate_3plus_k6": r3,
            "rate_4plus_k6": r4,
            "runtime_sec": 0.1,
            "failed": False,
        }
        for m, (r3, r4) in _TARGET_RATES.items()
        for p in pcts
    ]
    folds = root / "bench_results" / "folds.csv"
    folds.parent.mkdir(exist_ok=True)
    pd.DataFrame(rows).to_csv(folds, index=False)
    tested = sorted(_TARGET_RATES) + ([] if complete else ["ses_opt_alpha"])
    bm = root / "best_methods.json"
    bm.write_text(
        json.dumps(
            {
                "_meta": {
                    "methods_tested_per_game": {"loto_6_49": tested},
                    "percentiles": list(pcts),
                },
                "games": {"loto_6_49": {"label": "Loto 6/49", "draw_n": 6, "pick_n": 6}},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(decision, "BENCH_HIT_TARGET", 3)
    decision.update_best_methods_with_auto_pilot(str(bm), str(folds))
    # Directorul curent = rădăcina, ca la START_8000 (căile implicite ale deciziei).
    monkeypatch.chdir(root)
    return bm


def _k6(bm):
    import json

    cells = json.loads(bm.read_text(encoding="utf-8"))["games"]["loto_6_49"][
        "auto_pilot_per_pool"
    ]
    return cells["k6"]["scorer"], cells["k6"]["hit_target"]


def _startup_without_queue(monkeypatch):
    for name in ("init_job_queue", "_migrate_legacy_finalized_marker", "_recover_completed_job"):
        monkeypatch.setattr(app, name, Mock())
    monkeypatch.setattr(app, "is_fresh_ui_start", lambda: True)
    monkeypatch.setattr(app, "cancel_pending_running_jobs", Mock(return_value=0))
    monkeypatch.setattr(app, "_bench_running", lambda: False)
    monkeypatch.setattr(app, "_after_server_start", lambda fn: fn())


def test_startup_does_not_hold_the_port_while_rebuilding(
    sidebar_context, monkeypatch, tmp_path
):
    """Recalcularea (~10 s) nu rulează în lifespan: portul se deschide întâi."""
    _client, settings, _state = sidebar_context
    bm = _decision_built_for_3(monkeypatch, tmp_path)
    before = bm.read_bytes()
    settings["bench_hit_target"] = 4
    _startup_without_queue(monkeypatch)
    scheduled = []
    monkeypatch.setattr(app, "_after_server_start", scheduled.append)
    app._startup()
    assert bm.read_bytes() == before
    assert len(scheduled) == 1
    scheduled[0]()
    assert _k6(bm) == ("markov_lag2", 4)


def test_startup_rebuilds_a_decision_built_for_another_target(
    sidebar_context, monkeypatch, tmp_path
):
    _client, settings, _state = sidebar_context
    bm = _decision_built_for_3(monkeypatch, tmp_path)
    assert _k6(bm) == ("frequency", 3)
    settings["bench_hit_target"] = 4  # 4+ salvat în UI
    _startup_without_queue(monkeypatch)
    app._startup()
    assert decision.BENCH_HIT_TARGET == 4
    assert _k6(bm) == ("markov_lag2", 4)
    import json

    assert json.loads(bm.read_text(encoding="utf-8"))["_meta"]["bench_hit_target"] == 4


def test_startup_keeps_an_incomplete_decision_and_warns_until_rebench(
    sidebar_context, monkeypatch, tmp_path
):
    client, settings, _state = sidebar_context
    bm = _decision_built_for_3(monkeypatch, tmp_path, complete=False)
    before = bm.read_bytes()
    settings["bench_hit_target"] = 4
    _startup_without_queue(monkeypatch)
    app._startup()
    # folds.csv nu acoperă bench-ul deciziei: decizia rămâne cea veche...
    assert bm.read_bytes() == before
    # ... iar panoul o spune, în locul lui „Benchmark la zi”.
    monkeypatch.setattr(app, "_new_draws_summary", lambda: _fresh_summary())
    app._bench_freshness_panel("RO")
    text = "\n".join(_texts(client))
    assert "nu e pe ținta selectată (4+): Loto 6/49 pe 3+" in text
    assert "Benchmark la zi" not in text


def test_bench_finished_after_ui_restart_rebuilds_for_the_selected_target(
    sidebar_context, monkeypatch, tmp_path
):
    _client, settings, state = sidebar_context
    bm = _decision_built_for_3(monkeypatch, tmp_path)
    # UI-ul repornit cât rula bench-ul: marcajul din memorie s-a pierdut.
    settings["bench_hit_target"] = 4
    monkeypatch.setattr(decision, "BENCH_HIT_TARGET", 4)
    assert "bench_target_pending" not in state
    monkeypatch.setattr(app, "_new_draws_summary", lambda: _fresh_summary())
    app._on_bench_finished()
    assert _k6(bm) == ("markov_lag2", 4)


def test_startup_defers_the_rebuild_while_a_bench_runs(
    sidebar_context, monkeypatch, tmp_path
):
    _client, settings, state = sidebar_context
    bm = _decision_built_for_3(monkeypatch, tmp_path)
    before = bm.read_bytes()
    settings["bench_hit_target"] = 4
    _startup_without_queue(monkeypatch)
    monkeypatch.setattr(app, "_bench_running", lambda: True)
    app._startup()
    assert bm.read_bytes() == before  # folds.csv e parțial cât rulează bench-ul
    assert state.get("bench_target_pending") is True


def test_matching_decision_keeps_the_fresh_banner(
    sidebar_context, monkeypatch, tmp_path
):
    client, _settings, _state = sidebar_context
    bm = _decision_built_for_3(monkeypatch, tmp_path)
    before = bm.read_bytes()
    _startup_without_queue(monkeypatch)
    app._startup()
    assert bm.read_bytes() == before
    monkeypatch.setattr(app, "_new_draws_summary", lambda: _fresh_summary())
    app._bench_freshness_panel("RO")
    text = "\n".join(_texts(client))
    assert "Benchmark la zi" in text
    assert "ținta selectată" not in text


@pytest.mark.parametrize("country_meta, warned", [("AT", True), ("PL", False)])
def test_foreign_panel_warns_only_for_the_country_own_decision(
    sidebar_context, monkeypatch, tmp_path, country_meta, warned
):
    import json

    client, settings, _state = sidebar_context
    settings["bench_hit_target"] = 4
    dp = tmp_path / "decisions" / "AT" / "best_methods.json"
    dp.parent.mkdir(parents=True)
    dp.write_text(
        json.dumps(
            {
                "_meta": {"country": country_meta, "bench_hit_target": 3},
                "games": {
                    "at_lotto": {
                        "draw_n": 6,
                        "auto_pilot_per_pool": {"k6": {"scorer": "frequency", "hit_target": 3}},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        app, "_country_bench_texts", lambda cc: {"freshness": "✅ Austria la zi"}
    )
    app._bench_freshness_panel("AT")
    text = "\n".join(_texts(client))
    # Decizia altei țări e tratată de producție ca lipsă: niciun avertisment de țintă.
    assert ("Lotto 6 aus 45 pe 3+" in text) is warned
    assert ("✅ Austria la zi" in text) is not warned
