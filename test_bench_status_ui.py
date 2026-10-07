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
