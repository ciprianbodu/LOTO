"""Regresii UI 2026-11-01:

1. Secțiunea „📜 Istoric hits 4+ (walk-forward)” trebuie să apară în
   „Analiză & Clasament” ȘI când walk-forward a fost sărit (ex. pool peste 19
   fără plafon de variante) — deschisă, cu motivul înăuntru; până acum apărea
   doar o notă separată și secțiunea dispărea complet.
2. Eticheta câmpului de pool „(6–45)” putea fi citită ca geometrie „6/45” la
   Joker (urna 1 e 5/45); acum spune explicit „(6–45 numere)”.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import app_nicegui as app
import ui_hits
from scripts.analysis.audit_output import capture_ui


def _game_data() -> dict:
    return {
        "hard_core": list(range(1, 31)),
        "pool_size": 30,
        "guarantee": 4,
        "variants": [[1, 2, 3, 4, 5, 6]],
        "context": {"coverage_pct": 100.0},
        "audit": {
            "bench_winner": {"loto_6_49": {"method": "frequency", "pool_hint": 20}},
        },
    }


def _patch_heavy_renderers(monkeypatch):
    monkeypatch.setattr(ui_hits, "_render_bench_leaderboard", lambda *a, **k: None)
    monkeypatch.setattr(ui_hits, "_render_last_csv_draw", lambda *a, **k: None)
    monkeypatch.setattr(ui_hits, "_bench_target", lambda: 4)


def _hits_expansions(ui):
    return [
        n
        for n in ui.walk()
        if n["kind"] == "expansion" and "Istoric hits" in str(n["args"][0])
    ]


def test_hits_section_appears_even_when_wf_skipped_large_pool(monkeypatch):
    _patch_heavy_renderers(monkeypatch)
    monkeypatch.setitem(app.STATE, "retro", {})
    monkeypatch.setitem(
        app.STATE,
        "retro_meta",
        {
            "loto_6_49.csv_6/49": {
                "large_pool_wf_skipped": True,
                "skip_reason": "variant_cap_required",
            }
        },
    )
    with capture_ui() as ui:
        ui_hits._render_game_analysis("loto_6_49.csv", "6/49", _game_data())
    exps = _hits_expansions(ui)
    assert exps, "secțiunea 📜 Istoric hits lipsește când WF e sărit"
    assert exps[0]["kwargs"].get("value") is True, "secțiunea trebuie deschisă"
    text = ui.text()
    assert "Walk-forward necalculat pentru pool-ul mare" in text
    assert "Stabilește un plafon de variante" in text
    assert "Niciun istoric walk-forward pentru această cerere" in text


def test_hits_section_with_wf_data_still_renders(monkeypatch):
    _patch_heavy_renderers(monkeypatch)
    flat = [
        SimpleNamespace(
            draw_index=i,
            draw_date=f"{1 + i:02d}-09-2026",
            hits_union=4,
            hits=4,
            wheel_coverage=100.0,
            variant=[2, 10, 20, 30, 40, 49],
            ticket_context=None,
        )
        for i in range(5)
    ]
    monkeypatch.setitem(app.STATE, "retro", {"loto_6_49.csv_6/49": flat})
    monkeypatch.setitem(app.STATE, "retro_meta", {})
    with capture_ui() as ui:
        ui_hits._render_game_analysis("loto_6_49.csv", "6/49", _game_data())
    exps = _hits_expansions(ui)
    assert exps, "secțiunea 📜 Istoric hits lipsește când WF a rulat"
    assert exps[0]["kwargs"].get("value") is True
    assert any(n["kind"] == "table" for n in exps[0]["children"]), "tabelul WF lipsește"


def test_pool_input_label_says_numere_not_geometry():
    """„Pool Joker (Urna 1) (6–45)” era citibil ca „6/45”; acum spune „numere”."""
    src = Path("app_nicegui.py").read_text(encoding="utf-8")
    assert 'f"{_ptitle} (6–{_pmax} numere)"' in src
    assert 'f"{_ptitle} (6–{_pmax})"' not in src
