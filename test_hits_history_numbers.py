"""„📜 Istoric hits” arată pool-ul și biletul care au dat hiturile fiecărei extrageri.

La cererea utilizatorului (2026-10-17): lângă „5 hits” trebuie să se vadă cu ce
pool s-au prins. Pool-ul e cel din `ticket_context` al pasului walk-forward,
ales la data extragerii numai din extragerile de dinainte.
"""

from __future__ import annotations

import app_nicegui  # noqa: F401  (importul leagă numele UI, ca aplicația)
import ui_hits
from loto_enterprise.core.walk_forward_adapter import WalkForwardResult
from scripts.analysis.audit_output import capture_ui

POOL = [2, 5, 9, 12, 14, 17, 21, 23, 26, 30, 33, 36, 40, 42, 45, 48]


def _entries(game="6/49", context=True):
    """Trei extrageri: 5 în pool (bilet cu 5), 3 în pool, 1 în pool."""
    draws = {
        10: (
            "06-08-2026",
            [5, 12, 23, 33, 45, 1],
            [[5, 12, 23, 33, 45, 2], [9, 14, 17, 21, 26, 30]],
        ),
        11: (
            "09-08-2026",
            [2, 9, 40, 3, 4, 6],
            [[2, 9, 40, 5, 12, 14], [17, 21, 23, 26, 30, 33]],
        ),
        12: ("13-08-2026", [2, 3, 4, 6, 7, 8], [[2, 5, 9, 12, 14, 17]]),
    }
    flat = []
    for di, (date, drawn, tickets) in draws.items():
        if game == "joker":  # Urna 1: 5 extrase, bilete de 5 + numărul Joker
            drawn, tickets = drawn[:5], [t[:5] for t in tickets]
        pool_hits = len(set(POOL) & set(drawn))
        ctx = (
            {"hard_core": list(reversed(POOL)), "actual": sorted(drawn)}
            if context
            else None
        )
        for t in tickets:
            variant = t + [7] if game == "joker" else t
            flat.append(
                WalkForwardResult(
                    draw_index=di,
                    draw_date=date,
                    variant=variant,
                    hits=len(set(t) & set(drawn)),
                    hits_union=pool_hits,
                    wheel_coverage=100.0,
                    joker_hit=(di == 11) if game == "joker" else None,
                    ticket_context=ctx,
                )
            )
    return flat


def _tables(ui):
    return [n for n in ui.walk() if n["kind"] == "table"]


def _table_after(ui, title):
    nodes = list(ui.walk())
    for i, n in enumerate(nodes):
        if n["kind"] == "label" and n["args"] and str(n["args"][0]).startswith(title):
            return next(m for m in nodes[i:] if m["kind"] == "table")
    raise AssertionError(f"lipsește tabelul {title}")


def test_pool_table_shows_the_pool_and_the_draw_of_each_hit():
    with capture_ui() as ui:
        ui_hits._render_hits_4plus(_entries(), "6/49", {"pool_size": 16}, pool_n=16)
    table = _table_after(ui, "🗓️ POOL")
    names = [c["name"] for c in table["kwargs"]["columns"]]
    assert names == ["draw", "hits", "drawn", "pool_nums", "gap"]
    five = next(r for r in table["kwargs"]["rows"] if r["draw"] == "06-08-2026")
    assert "5" in five["hits"]
    # Pool-ul de atunci, crescător, cu cele 5 numere ieșite marcate.
    assert five["pool_nums"] == (
        "2 [5] 9 [12] 14 17 21 [23] 26 30 [33] 36 40 42 [45] 48"
    )
    assert [m["v"] for m in five["pool_nums_marks"] if m["hit"]] == [5, 12, 23, 33, 45]
    assert five["drawn"] == "1 [5] [12] [23] [33] [45]"
    assert len(table["kwargs"]["rows"]) == 1  # numai extragerile cu ≥4 în pool
    text = ui.text()
    assert (
        "„Pool-ul de atunci” = cele 16 numere alese de metodă la data extragerii"
        in text
    )
    assert "nu pool-ul de azi" in text
    assert "dintr-o validare salvată înainte" not in text


def test_ticket_tables_show_the_best_ticket_without_the_joker_number():
    with capture_ui() as ui:
        ui_hits._render_hits_4plus(
            _entries("joker"), "joker", {"pool_size": 16}, pool_n=16
        )
    table = _table_after(ui, "🎟️ VARIANTĂ 5+")
    (row,) = table["kwargs"]["rows"]
    # Biletul cu 5 prinse, fără numărul Joker (7) din Urna 2.
    assert row["ticket"] == "[5] [12] [23] [33] [45]"
    assert [m["v"] for m in row["ticket_marks"]] == [5, 12, 23, 33, 45]
    assert row["drawn"] == "[5] [12] [23] [33] [45]"


def test_old_cache_steps_without_context_say_so():
    with capture_ui() as ui:
        ui_hits._render_hits_4plus(
            _entries(context=False), "6/49", {"pool_size": 16}, pool_n=16
        )
    table = _table_after(ui, "🗓️ POOL")
    assert all(
        r["pool_nums"] == "—" and r["pool_nums_marks"] == []
        for r in table["kwargs"]["rows"]
    )
    assert (
        "dintr-o validare salvată înainte ca pool-ul pasului să fie păstrat"
        in ui.text()
    )


def test_marked_numbers_helper():
    assert ui_hits._wf_marked_numbers([], [1]) == ("—", [])
    text, marks = ui_hits._wf_marked_numbers([3, 7, 9], [7])
    assert text == "3 [7] 9"
    assert marks == [
        {"v": 3, "hit": False},
        {"v": 7, "hit": True},
        {"v": 9, "hit": False},
    ]


def _title(ui, prefix):
    return next(
        str(n["args"][0])
        for n in ui.walk()
        if n["kind"] == "label" and n["args"] and str(n["args"][0]).startswith(prefix)
    )


def test_pool_title_counts_each_level_and_the_filter_keeps_the_fives():
    # Utilizatorul a văzut un singur 5 pe prima pagină din 16; titlul spune
    # acum câte extrageri are fiecare nivel, iar filtrul le arată pe toate.
    entries = _entries() + [
        WalkForwardResult(
            draw_index=13,
            draw_date="16-08-2026",
            variant=[2, 5, 9, 12, 14, 17],
            hits=4,
            hits_union=4,
            wheel_coverage=100.0,
            ticket_context={"hard_core": POOL, "actual": [2, 5, 9, 12, 1, 3]},
        )
    ]
    with capture_ui() as ui:
        ui_hits._render_hits_4plus(entries, "6/49", {"pool_size": 16}, pool_n=16)
    assert _title(ui, "🗓️ POOL") == (
        "🗓️ POOL (2 extrageri: 1 cu 4, 1 cu 5; cele mai recente întâi):"
    )
    nodes = list(ui.walk())
    start = next(
        i
        for i, n in enumerate(nodes)
        if str((n["args"] or [""])[0]).startswith("🗓️ POOL")
    )
    toggle = next(n for n in nodes[start:] if n["kind"] == "toggle")
    table = next(n for n in nodes[start:] if n["kind"] == "table")
    assert toggle["args"][0] == {4: "≥4", 5: "≥5"}
    assert toggle["kwargs"]["value"] == 4

    class _Event:
        value = 5

    toggle["kwargs"]["on_change"](_Event())
    assert [r["draw"] for r in table["attrs"]["rows"]] == ["06-08-2026"]
    assert table["attrs"]["pagination"]["page"] == 1  # filtrul pornește de la pagina 1
    _Event.value = 4
    toggle["kwargs"]["on_change"](_Event())
    assert [r["draw"] for r in table["attrs"]["rows"]] == ["16-08-2026", "06-08-2026"]


def test_single_level_tables_have_no_filter_and_singular_title():
    with capture_ui() as ui:
        ui_hits._render_hits_4plus(_entries(), "6/49", {"pool_size": 16}, pool_n=16)
    assert (
        _title(ui, "🎟️ VARIANTĂ 5+")
        == "🎟️ VARIANTĂ 5+ (1 extragere; cele mai recente întâi):"
    )
    assert (
        _title(ui, "🎟️ VARIANTĂ 4+")
        == "🎟️ VARIANTĂ 4+ (1 extragere; cele mai recente întâi):"
    )
    assert sum(n["kind"] == "toggle" for n in ui.walk()) == 0  # un singur nivel, 5


def test_joker_number_hits_are_shown_in_the_panel():
    with capture_ui() as ui:
        ui_hits._render_hits_4plus(
            _entries("joker"), "joker", {"pool_size": 16}, pool_n=16
        )
    assert (
        "🃏 Numărul Joker (Urna 2, un singur număr pe bilet): nimerit la 1 din 3 "
        "extrageri (33.33%; la întâmplare 5%)."
    ) in ui.text()
    with capture_ui() as ui:
        ui_hits._render_hits_4plus(_entries(), "6/49", {"pool_size": 16}, pool_n=16)
    assert "Numărul Joker" not in ui.text()
