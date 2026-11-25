"""Rendered odds, reports and generation state reflect the actual saved tickets."""
import asyncio
import json
from unittest.mock import Mock

import pandas as pd
import pytest
from nicegui import Client, ui

import app_nicegui as app
import ui_nested_wfv
import ui_results


def test_changed_games_load_their_histories_and_empty_choice_matches_generation(monkeypatch):
    monkeypatch.setitem(app.SETTINGS, "country_val", "RO")
    monkeypatch.setitem(app.SETTINGS, "games_val", ["6/49"])
    monkeypatch.setattr(app, "_save_settings", Mock())
    autoload = Mock(return_value=([], []))
    monkeypatch.setattr(app, "_autoload_histories", autoload)

    async def scenario():
        client = Client(ui.page("/test-selected-generation-games"))
        try:
            with client:
                selector = app._generation_games_selector("RO", Mock())
                assert selector.value == ["6/49"]
                selector.set_value(["6/49", "5/40"])
                assert [game.game_id for game in autoload.call_args.args[0]] == ["6/49", "5/40"]
                assert app.SETTINGS["games_val"] == selector.value
                selector.set_value([])
                assert set(selector.value) == set(app.SETTINGS["games_val"]) == {"6/49", "5/40", "joker"}
                assert set(selector.value) == {game.game_id for game in app._selected_games()}
        finally:
            client.delete()

    asyncio.run(scenario())


@pytest.mark.parametrize("pool_size", [8, 25])
@pytest.mark.parametrize(("suffixes", "odds", "extra"), [
    ([[], []], "Nicio variantă nu are o bilă Joker", ""),
    ([[4], [4]], "1/20 (5%)", ""),
    ([[4], [17]], "2/20 (10%)", ""),
    ([[4], []], "1/20 (5%)", "fără bilă Joker: 1"),
    ([[True], [4]], "bilă Joker invalidă", ""),
    ([[21], [4]], "bilă Joker invalidă", ""),
])
def test_displayed_joker_chance_tracks_the_actual_variants(pool_size, suffixes, odds, extra):
    data = dict(hard_core=list(range(1, pool_size + 1)), hard_core_joker=[1, 2, 3],
                variants=[[1, 2, 3, 4, 5] + suffix for suffix in suffixes])
    lines = ui_results._wheel_probability_lines("joker", data)
    line, = [line for line in lines if line.startswith("Urna 2")]
    assert odds in line and extra in line


@pytest.mark.parametrize("pool_size", [8, 25])
@pytest.mark.parametrize("variant", [[1, 2, 3, 4], [1, 2, 3, 4, 5, 6, 7]])
def test_invalid_joker_ticket_width_does_not_produce_odds(pool_size, variant):
    data = dict(hard_core=list(range(1, pool_size + 1)), variants=[variant])
    assert ui_results._wheel_probability_lines("joker", data) == [
        "Șanse teoretice indisponibile: pool sau variante invalide."
    ]


@pytest.mark.parametrize(("game", "pool_size", "code", "row"), [
    ("6/49", 10, 49, [1, 2, 3, 4, 5, 6]),
    ("joker", 10, 24, [1, 2, 3, 4, 5]),
])
def test_email_does_not_certify_a_code_disproved_by_the_generated_tickets(monkeypatch, game, pool_size, code, row):
    from covering.lr_selection import resolve_lr_scheme
    from covering.lr_schemes import scheme_tickets

    pool = list(range(1, pool_size + 1))
    scheme = resolve_lr_scheme(game, code)
    data = {"lr_scheme_code": code, "hard_core": pool, "variants": [list(row) for _ in range(scheme.size)]}
    snapshot = app._mail_snapshot_inputs(([("history.csv", {game: data})], {}), {})
    monkeypatch.setattr(app, "_prepare_mail_alternatives", lambda _: None)
    monkeypatch.setattr(app, "_mail_method_lines", lambda *args, **kwargs: [])
    frozen = snapshot["games"][0]["data"]
    assert "variants" not in frozen and frozen["lr_layout_mismatch"] is True
    if game == "joker":
        assert "dacă se completează câmpul Joker" in ui_results._generated_system_summary(game, data)
    data["variants"] = [list(v) + ([3] if game == "joker" else []) for v in scheme_tickets(scheme, pool)]
    report = app._build_mail_body(snapshot)
    assert "SCHEMĂ LR:" not in report and f"Codul LR {code} din rezultat nu corespunde" in report
    assert f"VARIANTE GENERATE: {scheme.size} " in report
    if game == "joker":
        assert f"Variante fără bilă Joker validă: {scheme.size}" in report
    certified = app._mail_snapshot_inputs(([("history.csv", {game: data})], {}), {})
    assert f"SCHEMĂ LR: Cod {code}" in app._build_mail_body(certified)


def test_failed_submission_preserves_displayed_result_and_walk_forward(monkeypatch):
    history = pd.DataFrame({"date": ["19-11-2026"], **{f"n{i}": [i] for i in range(1, 7)}})
    original = dict(datasets=[("loto_6_49.csv", history)], results=object(), result_sources=object(),
                    wf_running=True, wf_seq=11, retro={"loto_6_49": [1]}, active_job_id=None)
    for key, value in original.items():
        monkeypatch.setitem(app.STATE, key, value)
    monkeypatch.setitem(app.STATE, "virtual_wf_running", False)
    monkeypatch.setitem(app.SETTINGS, "country_val", "RO")
    monkeypatch.setitem(app.SETTINGS, "games_val", ["6/49"])
    monkeypatch.setattr(app, "submit_job", Mock(side_effect=OSError("unavailable queue")))
    notify, worker = Mock(), Mock()
    monkeypatch.setattr(app.ui, "notify", notify)
    monkeypatch.setattr(app, "ensure_worker_running", worker)
    app.submit_generation()
    app.submit_job.assert_called_once()
    assert all(app.STATE[key] is value for key, value in original.items())
    worker.assert_not_called()
    assert notify.call_args.kwargs["type"] == "negative"


@pytest.mark.parametrize(("corrupt", "notice"), [
    ([], "nu poate fi citit"),
    ({"results": []}, "nu poate fi citit"),
    ({"results": {"loto_6_49": {"key": "loto_6_49"}}}, "pentru acest joc este incomplet"),
])
def test_malformed_saved_validation_keeps_the_page_available(tmp_path, monkeypatch, corrupt, notice):
    path = tmp_path / "validation.json"
    path.write_text(json.dumps(corrupt), encoding="utf-8")
    monkeypatch.setattr(ui_nested_wfv, "validation_path", lambda country: path)
    monkeypatch.setattr(ui, "timer", Mock())

    async def scenario():
        client = Client(ui.page("/test-saved-report-integrity"))
        try:
            with client:
                ui_nested_wfv.nested_wfv_panel("RO")
                assert any(isinstance(node, ui.select) for node in client.elements.values())
                assert any(isinstance(node, ui.label) and notice in node.text for node in client.elements.values())
        finally:
            client.delete()

    asyncio.run(scenario())
