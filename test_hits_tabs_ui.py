"""Organizarea pe file (tab-uri) a panoului „📜 Istoric hits” și a meniului de analiză.

La cererea utilizatorului (2026-10-31): în „Istoric hits” nu mai sunt amestecate
toate tabelele într-o singură coloană — sumarul +4/+5 rămâne sus, iar tabelele
detaliate (Pool, Variante 4+ și 5+, Bilet complet, Schemă LR) sunt pe câte o filă.
La nivelul „📊 Analiză & Clasament”, fiecare joc are propria filă, nu toate
clasamentele și istoricele stivuite.
"""

from __future__ import annotations

import app_nicegui  # noqa: F401  (importul leagă numele UI, ca aplicația)
import ui_hits
from loto_enterprise.core.walk_forward_adapter import WalkForwardResult
from scripts.analysis.audit_output import capture_ui

POOL = [2, 5, 9, 12, 14, 17, 21, 23, 26, 30, 33, 36, 40, 42, 45, 48]


def _entries(game="6/49"):
    """Două extrageri: 5 în pool (bilet cu 5), 3 în pool."""
    draws = {
        10: ("06-10-2026", [5, 12, 23, 33, 45, 1], [[5, 12, 23, 33, 45, 2]]),
        11: ("09-10-2026", [2, 9, 40, 3, 4, 6], [[2, 9, 40, 5, 12, 14]]),
    }
    flat = []
    for di, (date, drawn, tickets) in draws.items():
        pool_hits = len(set(POOL) & set(drawn))
        ctx = {"hard_core": list(reversed(POOL)), "actual": sorted(drawn)}
        for t in tickets:
            flat.append(
                WalkForwardResult(
                    draw_index=di,
                    draw_date=date,
                    variant=t,
                    hits=len(set(t) & set(drawn)),
                    hits_union=pool_hits,
                    wheel_coverage=100.0,
                    joker_hit=None,
                    ticket_context=ctx,
                )
            )
    return flat


def _descendants(node):
    out = []

    def visit(n):
        for c in n["children"]:
            out.append(c)
            visit(c)

    visit(node)
    return out


def _panel(ui, name):
    panels = [n for n in ui.walk() if n["kind"] == "tab_panel" and n["args"][0] == name]
    assert len(panels) == 1, f"fila {name} lipsește sau e duplicată"
    return panels[0]


def test_hits_history_tables_are_on_tabs_and_summary_stays_first():
    with capture_ui() as ui:
        ui_hits._render_hits_4plus(_entries(), "6/49", {"pool_size": 16}, pool_n=16)
    nodes = list(ui.walk())
    # Sumarul (+4/+5) rămâne primul tabel, deasupra filelor.
    summary = next(n for n in nodes if n["kind"] == "table")
    assert [r["prag"] for r in summary["kwargs"]["rows"]] == ["+4", "+5"]
    # O singură bară de file, cu câte o filă per secțiune (joc românesc → și LR).
    tabs = [n for n in nodes if n["kind"] == "tabs"]
    assert len(tabs) == 1
    tab_names = [t["args"][0] for t in tabs[0]["children"] if t["kind"] == "tab"]
    assert tab_names == ["pool", "var", "lr"]
    panels = [n for n in nodes if n["kind"] == "tab_panels"]
    assert len(panels) == 1 and panels[0]["kwargs"]["value"] == "pool"
    # Tabelul POOL e în fila „pool”, expansion-ul de variante în fila „var”.
    pool_panel = _panel(ui, "pool")
    assert any(
        n["kind"] == "label" and str(n["args"][0]).startswith("🗓️ POOL")
        for n in _descendants(pool_panel)
    )
    var_panel = _panel(ui, "var")
    assert any(
        n["kind"] == "expansion" and "Variante WF" in n["args"][0]
        for n in _descendants(var_panel)
    )
    # Fără full_ticket nu apare fila „bilet”.
    assert not [n for n in nodes if n["kind"] == "tab_panel" and n["args"][0] == "bilet"]


def test_full_ticket_adds_its_own_tab(monkeypatch):
    monkeypatch.setattr(ui_hits, "_render_full_ticket_replay", lambda *a, **k: None)
    with capture_ui() as ui:
        ui_hits._render_hits_4plus(
            _entries(),
            "6/49",
            {"pool_size": 16},
            pool_n=16,
            full_ticket={"game": "6/49", "guarantee": 4},
        )
    tab_names = [
        t["args"][0] for n in ui.walk() if n["kind"] == "tabs" for t in n["children"] if t["kind"] == "tab"
    ]
    assert tab_names == ["pool", "var", "bilet", "lr"]
    assert _panel(ui, "bilet") is not None


def _menu_bundle(*games):
    return [("loto_6_49.csv", {g: {} for g in games})]


def test_analysis_menu_puts_each_game_on_its_own_tab(monkeypatch, tmp_path):
    (tmp_path / "bench_results").mkdir()
    (tmp_path / "bench_results" / "folds.csv").write_text("", encoding="utf-8")
    monkeypatch.setattr(ui_hits, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(ui_hits, "STATE", {"retro": {}, "retro_meta": {}})
    calls = []
    monkeypatch.setattr(
        ui_hits, "_render_game_analysis", lambda *a: calls.append(a)
    )
    with capture_ui() as ui:
        ui_hits._render_analysis_menu(_menu_bundle("6/49", "5/40", "joker"))
    tabs = [n for n in ui.walk() if n["kind"] == "tabs"]
    assert len(tabs) == 1
    labels = [t["args"][1] for t in tabs[0]["children"] if t["kind"] == "tab"]
    assert labels == ["🎯 România · Loto 6/49", "🎯 România · Joker", "🎯 România · Loto 5/40"]
    assert [c[1] for c in calls] == ["6/49", "joker", "5/40"]
    # Fiecare joc e randat într-o filă proprie.
    panel_names = [n["args"][0] for n in ui.walk() if n["kind"] == "tab_panel"]
    assert panel_names == ["g0", "g1", "g2"]


def test_analysis_menu_with_one_game_has_no_tabs(monkeypatch, tmp_path):
    (tmp_path / "bench_results").mkdir()
    (tmp_path / "bench_results" / "folds.csv").write_text("", encoding="utf-8")
    monkeypatch.setattr(ui_hits, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(ui_hits, "STATE", {"retro": {}, "retro_meta": {}})
    calls = []
    monkeypatch.setattr(
        ui_hits, "_render_game_analysis", lambda *a: calls.append(a)
    )
    with capture_ui() as ui:
        ui_hits._render_analysis_menu(_menu_bundle("6/49"))
    assert not [n for n in ui.walk() if n["kind"] == "tabs"]
    assert len(calls) == 1 and calls[0][1] == "6/49"
