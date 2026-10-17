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
        10: ("06-08-2026", [5, 12, 23, 33, 45, 1], [[5, 12, 23, 33, 45, 2], [9, 14, 17, 21, 26, 30]]),
        11: ("09-08-2026", [2, 9, 40, 3, 4, 6], [[2, 9, 40, 5, 12, 14], [17, 21, 23, 26, 30, 33]]),
        12: ("13-08-2026", [2, 3, 4, 6, 7, 8], [[2, 5, 9, 12, 14, 17]]),
    }
    flat = []
    for di, (date, drawn, tickets) in draws.items():
        if game == "joker":  # Urna 1: 5 extrase, bilete de 5 + numărul Joker
            drawn, tickets = drawn[:5], [t[:5] for t in tickets]
        pool_hits = len(set(POOL) & set(drawn))
        ctx = {"hard_core": list(reversed(POOL)), "actual": sorted(drawn)} if context else None
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
    assert len(table["kwargs"]["rows"]) == 2  # numai extragerile cu ≥3 în pool
    text = ui.text()
    assert "„Pool-ul de atunci” = cele 16 numere alese de metodă la data extragerii" in text
    assert "nu pool-ul de azi" in text
    assert "dintr-o validare salvată înainte" not in text


def test_ticket_tables_show_the_best_ticket_without_the_joker_number():
    with capture_ui() as ui:
        ui_hits._render_hits_4plus(_entries("joker"), "joker", {"pool_size": 16}, pool_n=16)
    table = _table_after(ui, "🎟️ BILET 5+")
    (row,) = table["kwargs"]["rows"]
    # Biletul cu 5 prinse, fără numărul Joker (7) din Urna 2.
    assert row["ticket"] == "[5] [12] [23] [33] [45]"
    assert [m["v"] for m in row["ticket_marks"]] == [5, 12, 23, 33, 45]
    assert row["drawn"] == "[5] [12] [23] [33] [45]"


def test_old_cache_steps_without_context_say_so():
    with capture_ui() as ui:
        ui_hits._render_hits_4plus(_entries(context=False), "6/49", {"pool_size": 16}, pool_n=16)
    table = _table_after(ui, "🗓️ POOL")
    assert all(r["pool_nums"] == "—" and r["pool_nums_marks"] == [] for r in table["kwargs"]["rows"])
    assert "dintr-o validare salvată înainte ca pool-ul pasului să fie păstrat" in ui.text()


def test_marked_numbers_helper():
    assert ui_hits._wf_marked_numbers([], [1]) == ("—", [])
    text, marks = ui_hits._wf_marked_numbers([3, 7, 9], [7])
    assert text == "3 [7] 9"
    assert marks == [{"v": 3, "hit": False}, {"v": 7, "hit": True}, {"v": 9, "hit": False}]
