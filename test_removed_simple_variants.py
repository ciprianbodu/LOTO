"""Saved settings and existing results retain LR functionality after removal."""

import json

import app_nicegui  # Bind the shared UI namespace as in the application.
import ui_results
import ui_runtime
from covering.lr_schemes import lr_schemes, scheme_tickets
from scripts.analysis.audit_output import capture_ui


def test_old_simple_ticket_settings_are_ignored_and_dropped_on_save(tmp_path, monkeypatch):
    state = tmp_path / "ui_state.json"
    state.write_text(json.dumps({
        "simple_variants_val": 500,
        "full_ticket_count_val": 10,
        "full_ticket_spread_val": True,
        "guarantee_val": 3,
        "mail_on_complete": False,
    }), encoding="utf-8")
    settings = dict(ui_runtime.DEFAULTS)
    monkeypatch.setattr(ui_runtime, "UI_STATE_FILE", state)
    monkeypatch.setattr(ui_runtime, "SETTINGS", settings)
    monkeypatch.setenv("LOTO_BENCH_TARGET", "3")
    ui_runtime._load_settings()
    assert settings["guarantee_val"] == 3
    assert {g: ui_runtime._pool_size_for(g) for g in ("6/49", "joker", "5/40")} == {
        "6/49": 16, "joker": 15, "5/40": 19,
    }
    ui_runtime._save_settings()
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["guarantee_val"] == 3
    assert not {"simple_variants_val", "full_ticket_count_val", "full_ticket_spread_val"} & saved.keys()


def test_existing_result_displays_lr_layout_without_simple_ticket_controls(monkeypatch):
    scheme = next(s for s in lr_schemes("6/49", 16) if s.code == 59)
    pool = list(range(1, 17))
    variants = scheme_tickets(scheme, pool)
    data = {"hard_core": pool, "pool_size": 16, "guarantee": 3,
            "variants": variants, "audit": {}, "context": {}}
    with capture_ui() as ui:
        ui_results._render_pool_body("history.csv", "6/49", data)
    text = ui.text()
    assert "Cod 59" in text
    assert "112" in text
    assert "Cod 59 pe pool-ul de azi (112 variante)" in text
    assert "V  1: " + ", ".join(map(str, variants[0])) in text
    assert "Variante simple" not in text
    assert "bilete simple" not in text
    assert "Bilet complet" not in text
    buttons = [str(n["args"]) for n in ui.walk() if n["kind"] == "button"]
    assert not any("Arată toate" in b or "Ascunde" in b for b in buttons)
    assert data["variants"] == variants

    monkeypatch.setitem(ui_results.STATE, "results", ([("history.csv", {"6/49": data})], {}))
    monkeypatch.setitem(ui_results.STATE, "retro", {})
    monkeypatch.setitem(ui_results.STATE, "retro_meta", {})
    report = ui_results._build_report()
    assert "Cod 59" in report
    assert "--- Variante simple" not in report
    assert "V  1:" not in report
