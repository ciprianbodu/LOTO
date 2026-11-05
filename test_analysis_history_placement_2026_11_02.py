"""Istoricul Re-Bench este lângă clasamentul jocului, nu lângă un job vechi."""
from types import SimpleNamespace

import app_nicegui as app
import ui_hits
from scripts.analysis.audit_output import capture_ui
from test_virtual_history_ui_2026_11_01 import virtual_setup


def _descendants(node):
    for child in node["children"]:
        yield child
        yield from _descendants(child)


def _record(game_id, name, pool, country="RO"):
    return {"game_id": game_id, "game": "6/49", "name": name,
            "pool": pool, "meta": {"country": country}}


def _capture_helpers(monkeypatch, calls):
    monkeypatch.setattr(ui_hits, "_render_virtual_controls", lambda: None)
    monkeypatch.setattr(ui_hits, "_render_virtual_history", lambda records, **kw: ui_hits.ui.label(
        "Re-Bench " + ", ".join(f"{r['game_id']}:{r['pool']}" for r in records)))
    monkeypatch.setattr(ui_hits, "_render_bench_leaderboard", lambda game, **kw: calls.append((game, kw["pool_size"])))
    monkeypatch.setattr(ui_hits, "_render_game_analysis", lambda _f, g, _d, _p: ui_hits.ui.label("Vechi " + g))


def test_rebench_and_recovered_result_are_distinct_inside_game_tab(monkeypatch):
    records = [_record("6/49", "Loto 6/49", 16),
               {**_record("joker", "Joker", 12), "game": "joker"},
               {**_record("5/40", "Loto 5/40", 19), "game": "5/40"}]
    monkeypatch.setitem(app.STATE, "virtual_wf_results", records)
    monkeypatch.setitem(app.STATE, "virtual_wf_status", "Analiză istorică încheiată.")
    monkeypatch.setitem(app.STATE, "results_recovered", "job #28")
    calls = []
    _capture_helpers(monkeypatch, calls)
    old = {"pool_size": 30, "hard_core": list(range(1, 31))}
    with capture_ui() as view:
        ui_hits._render_analysis_menu([("old.csv", {"6/49": old})])
    sections = [n for n in view.walk() if n["kind"] == "expansion" and n["args"][0] == "📊 Analiză & Clasament"]
    assert len(sections) == 1
    nodes = list(_descendants(sections[0]))
    panels = [n for n in nodes if n["kind"] == "tab_panel"]
    assert len(panels) == 3
    labels = [[str(n["args"][0]) for n in _descendants(p) if n["kind"] == "label" and n["args"]] for p in panels]
    assert [item for panel in labels for item in panel if item.startswith("Re-Bench ")] == [
        "Re-Bench 6/49:16", "Re-Bench joker:12", "Re-Bench 5/40:19"]
    old_sections = [n for n in _descendants(panels[0]) if n["kind"] == "expansion" and "Rezultatul recuperat" in n["args"][0]]
    assert len(old_sections) == 1 and "pool 30" in old_sections[0]["args"][0]
    assert old_sections[0]["kwargs"]["value"] is False
    assert any(n["kind"] == "label" and n["args"][0] == "Vechi 6/49" for n in _descendants(old_sections[0]))
    assert calls == [("6/49", 16), ("joker", 12), ("5/40", 19)]


def test_rebench_without_generated_job_has_one_tab_per_game(monkeypatch):
    records = [_record("6/49", "Loto 6/49", 16),
               {**_record("joker", "Joker", 12), "game": "joker"}]
    monkeypatch.setitem(app.STATE, "virtual_wf_results", records)
    monkeypatch.setitem(app.STATE, "virtual_wf_status", "Analiză încheiată")
    monkeypatch.setitem(app.STATE, "results_recovered", None)
    calls = []
    _capture_helpers(monkeypatch, calls)
    with capture_ui() as view:
        ui_hits._render_analysis_menu([])
    panels = [n for n in view.walk() if n["kind"] == "tab_panel"]
    assert len(panels) == 2
    assert calls == [("6/49", 16), ("joker", 12)]


def test_same_geometry_in_two_countries_does_not_merge_histories(monkeypatch):
    records = [_record("6/49", "Loto 6/49", 16), _record("de_lotto", "Lotto 6aus49", 10, "DE")]
    monkeypatch.setitem(app.STATE, "virtual_wf_results", records)
    monkeypatch.setitem(app.STATE, "virtual_wf_status", "Analiză încheiată")
    monkeypatch.setitem(app.STATE, "results_recovered", None)
    calls = []
    _capture_helpers(monkeypatch, calls)
    with capture_ui() as view:
        ui_hits._render_analysis_menu([])
    assert len([n for n in view.walk() if n["kind"] == "tab_panel"]) == 2
    assert calls == [("6/49", 16), ("de_lotto", 10)]
    # Legacy errors lacked geometry; the name and country still identify them.
    legacy = {"name": "Lotto 6aus49", "meta": {"country": "DE"}, "error": "test"}
    assert ui_hits._analysis_record_spec(legacy).game_id == "de_lotto"


def test_countries_distinguish_tabs_with_identical_game_names(monkeypatch):
    records = [_record("pl_lotto", "Lotto", 10, "PL"), _record("be_lotto", "Lotto", 9, "BE")]
    monkeypatch.setitem(app.STATE, "virtual_wf_results", records)
    monkeypatch.setitem(app.STATE, "virtual_wf_status", "Analiză încheiată")
    monkeypatch.setitem(app.STATE, "results_recovered", None)
    _capture_helpers(monkeypatch, [])
    with capture_ui() as view:
        ui_hits._render_analysis_menu([])
    tab_names = [node["args"][1] for node in view.walk() if node["kind"] == "tab"]
    assert len(tab_names) == 2 and tab_names[0] != tab_names[1]
    assert any("Polonia" in name for name in tab_names)
    assert any("Belgia" in name for name in tab_names)


def test_prize_text_distinguishes_simulated_tickets_from_virtual_system(monkeypatch):
    import ui_prizes
    row = SimpleNamespace(draw_index=1, draw_date="29-10-2026", variant=[1, 2, 3, 4, 5, 6],
        hits=4, hits_union=4, variant_count=1, virtual_full_system=False,
        ticket_context={"actual": [1, 2, 3, 4, 40, 49], "hard_core": [1, 2, 3, 4, 5, 6]})
    record = {**_record("6/49", "Loto 6/49", 6), "guarantee": 3, "depth": 30,
              "flat": [row], "simulated": True}
    monkeypatch.setattr(ui_prizes.PoolPrizeBinding, "request", lambda *_a, **_kw: None)
    with capture_ui() as simulated:
        app._render_virtual_history([record], show_status=False)
    assert "nu stabilește că au fost jucate" in simulated.text()
    assert "Premiile afișate, când sunt disponibile, sunt estimări brute" in simulated.text()
    assert "nu atribuie premii" not in simulated.text()
    row.virtual_full_system = True
    record["simulated"] = False
    with capture_ui() as virtual:
        app._render_virtual_history([record], show_status=False)
    assert "nu se atribuie premii" in virtual.text()


def test_failed_walk_forward_keeps_game_identity_for_the_right_tab(virtual_setup, monkeypatch):
    from loto_enterprise.core import walk_forward_adapter as wf
    def fail(**_kwargs):
        raise ValueError("eroare de test")
    monkeypatch.setattr(wf, "run_honest_walk_forward", fail)
    assert app._start_virtual_history(automatic=True, simulated=True)
    records = app.STATE["virtual_wf_results"]
    assert [record["game_id"] for record in records] == ["6/49", "joker", "5/40"]
    assert [record["source"] for record in records] == ["6/49.csv", "joker.csv", "5/40.csv"]
    assert all(record["error"] == "eroare de test" for record in records)
