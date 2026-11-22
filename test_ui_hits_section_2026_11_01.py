"""Regresii UI 2026-11-01:

1. Secțiunea „📜 Istoricul variantelor (walk-forward)” trebuie să apară în
   „Analiză & Clasament” ȘI când walk-forward a fost sărit (ex. pool peste 19
   fără plafon de variante) — deschisă, cu motivul înăuntru; până acum apărea
   doar o notă separată și secțiunea dispărea complet.
2. Glisorul Joker afișează numărul ales, codul LR și costul.
"""

from __future__ import annotations

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
        if n["kind"] == "expansion" and "Istoricul variantelor" in str(n["args"][0])
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
    assert exps, "secțiunea 📜 Istoricul variantelor lipsește când WF e sărit"
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
    assert exps, "secțiunea 📜 Istoricul variantelor lipsește când WF a rulat"
    assert exps[0]["kwargs"].get("value") is True
    assert any(n["kind"] == "table" for n in exps[0]["children"]), "tabelul WF lipsește"


def test_pool_input_label_says_numere_not_geometry(monkeypatch):
    """The rendered LR slider names the pool as numbers, with its code and cost."""
    import ui_runtime

    for module in (app, ui_runtime):
        monkeypatch.setitem(module.SETTINGS, module._POOL_KEYS["joker"], 10)
        monkeypatch.setitem(module.SETTINGS, module._LR_CODE_KEYS["joker"], 24)
    client = app.ui.context.client
    before = set(client.elements)
    control = app._render_lr_pool_input("joker", "Pool Joker (Urna 1)")
    created = set(client.elements) - before
    try:
        text = "\n".join(str(getattr(client.elements[i], "text", "")) for i in created)
        assert "Pool Joker (Urna 1)" in text
        assert "Cod 24" in text and "10 numere" in text
        assert "14 variante" in text and "98 lei" in text
        assert "Garanții condiționate" in text
        assert "6/45" not in text and "(6–45)" not in text
        assert str(control._props["label-value"]) == "10"
    finally:
        for identifier in sorted(created, reverse=True):
            if identifier in client.elements:
                client.elements[identifier].delete()
