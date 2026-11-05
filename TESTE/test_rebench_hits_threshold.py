"""Re-Bench prize rows retain only pool hits 4+, with full summary denominators."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

import app_nicegui as app
import ui_prizes
from scripts.analysis.audit_output import capture_ui


def record(game="6/49", *, simulated=True, levels=None):
    drawn = 5 if game == "joker" else 6
    pick = 6 if game == "6/49" else 5
    levels = tuple(range(drawn + 1)) if levels is None else levels
    flat = []
    for index, hits in enumerate(levels):
        actual = list(range(1, hits + 1)) + list(range(30, 30 + drawn - hits))
        main = list(range(1, pick + 1))
        flat.append(SimpleNamespace(
            draw_index=index, draw_date=f"2026-10-{index + 1:02d}",
            hits_union=hits, hits=min(hits, pick), variant=main + ([7] if game == "joker" else []),
            variant_count=1, virtual_full_system=not simulated,
            ticket_context={"actual": actual, "hard_core": list(range(1, 17))},
        ))
    return {"name": game, "game": game, "pool": 16, "guarantee": pick,
            "depth": 30, "simulated": simulated, "flat": flat,
            "meta": {"n_test_draws": len(flat), "n_expected": len(flat)}}


@pytest.fixture
def bindings(monkeypatch):
    seen = []
    class Binding:
        def __init__(self, game, table, rows, records):
            seen.append({"game": game, "rows": rows, "records": records})
        def request(self, **kwargs): pass
        def refresh(self): pass
        def set_pagination(self, value): pass
    monkeypatch.setattr(ui_prizes, "PoolPrizeBinding", Binding)
    return seen


@pytest.mark.parametrize("game", ["6/49", "joker", "5/40"])
@pytest.mark.parametrize("simulated", [True, False])
@pytest.mark.parametrize("bench_target", [3, 4])
def test_rebench_prize_table_shows_only_four_five_six_pool_hits(bindings, monkeypatch, game, simulated, bench_target):
    monkeypatch.setitem(app.SETTINGS, "bench_hit_target", bench_target)
    source = record(game, simulated=simulated)
    original = deepcopy(source)
    with capture_ui() as view:
        app._render_virtual_history([source], show_status=False)
    tables = [n for n in view.walk() if n["kind"] == "table"]
    detail = next(n for n in tables if n["kwargs"].get("row_key") == "draw_index")
    expected = [5, 4] if game == "joker" else [6, 5, 4]
    assert [row["pool_hits"] for row in detail["kwargs"]["rows"]] == expected
    assert all(row["pool_hits"] >= 4 for row in bindings[0]["rows"])
    assert set(bindings[0]["records"]) == {row["draw_index"] for row in bindings[0]["rows"]}
    summary = tables[0]["kwargs"]["rows"]
    total = len(source["flat"])
    assert summary[0]["pool"] == f"{len(expected)} ({len(expected) / total * 100:.2f}%)"
    assert [row["threshold"] for row in summary] == (["4+", "5+"] if game == "joker" else ["4+", "5+", "6+"])
    if game == "5/40":
        assert summary[-1]["ticket"] == "—"
    assert source == original


@pytest.mark.parametrize("game", ["6/49", "joker", "5/40"])
@pytest.mark.parametrize("simulated", [True, False])
def test_rebench_without_four_plus_has_no_prize_rows_or_requests(bindings, game, simulated):
    with capture_ui() as view:
        app._render_virtual_history([record(game, simulated=simulated, levels=(0, 1, 2, 3))], show_status=False)
    assert "Nu există extrageri cu 4+ în pool" in view.text()
    assert not any(n["kind"] == "table" and n["kwargs"].get("row_key") == "draw_index" for n in view.walk())
    assert not bindings
    summary = next(n for n in view.walk() if n["kind"] == "table")
    assert all(row["pool"] == "0 (0.00%)" for row in summary["kwargs"]["rows"])


def test_visible_pool_hit_keeps_all_generated_variants_for_its_actual_prize(bindings):
    source = record(levels=(2, 4))
    source["pool"] = 30  # No official LR code: retain the simulated variants.
    for entry in source["flat"]:
        entry.ticket_context["hard_core"] = list(range(1, 31))
    first = source["flat"][-1]
    first.hits = 3
    first.variant = [1, 2, 3, 10, 11, 12]
    second = deepcopy(first)
    second.hits = 2
    second.variant = [1, 2, 10, 11, 12, 13]
    source["flat"].append(second)
    with capture_ui() as view:
        app._render_virtual_history([source], show_status=False)
    visible = bindings[0]["rows"]
    assert len(visible) == 1 and visible[0]["pool_hits"] == 4
    generated = bindings[0]["records"][first.draw_index]["generated_variants"]
    assert generated == [first.variant, second.variant]
