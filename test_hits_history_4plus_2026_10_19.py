"""History displays 4+ across games and retains the full WF denominator."""

from types import SimpleNamespace

import pytest

import app_nicegui  # noqa: F401
import ui_hits
from loto_enterprise.core.walk_forward_adapter import WalkForwardResult
from scripts.analysis.audit_output import capture_ui


def entries(game, levels=(3, 4, 5)):
    draw_n = 5 if game == "joker" else 6
    play_n = 5 if game in {"joker", "5/40"} else 6
    result = []
    for i, hits in enumerate(levels):
        drawn = list(range(1, hits + 1)) + list(range(20, 20 + draw_n - hits))
        ticket = list(range(1, play_n + 1)) + ([7] if game == "joker" else [])
        result.append(
            WalkForwardResult(
                draw_index=i,
                draw_date=f"{i + 1:02d}-10-2026",
                variant=ticket,
                hits=min(hits, play_n),
                hits_union=hits,
                wheel_coverage=100.0,
                joker_hit=(i == 1) if game == "joker" else None,
                ticket_context={"hard_core": list(range(1, 17)), "actual": drawn},
            )
        )
    return result


@pytest.mark.parametrize("game", ["6/49", "5/40", "joker", "bg_toto2", "at_lotto"])
@pytest.mark.parametrize("bench_target", [3, 4])
def test_only_four_plus_rows_are_visible_without_changing_rates_or_bench(
    monkeypatch, game, bench_target
):
    monkeypatch.setattr(ui_hits, "_bench_target", lambda: bench_target)
    has_six = game != "joker"
    levels = (3, 4, 5, 6) if has_six else (3, 4, 5)
    with capture_ui() as ui:
        ui_hits._render_hits_4plus(entries(game, levels), game, {"pool_size": 16}, pool_n=16)
    nodes = list(ui.walk())
    summary = next(n for n in nodes if n["kind"] == "table")
    rows = summary["kwargs"]["rows"]
    if has_six:
        assert [row["prag"] for row in rows] == ["+4", "+5", "+6"]
        assert [row["pool"] for row in rows] == ["3 (75.00%)", "2 (50.00%)", "1 (25.00%)"]
        # At 5/40 all six drawn numbers can be in the pool, but a ticket has five.
        assert rows[-1]["tick"] == ("—" if game == "5/40" else "1 (25.00%)")
    else:
        assert [row["prag"] for row in rows] == ["+4", "+5"]
        assert [row["pool"] for row in rows] == ["2 (66.67%)", "1 (33.33%)"]
    start = next(
        i
        for i, n in enumerate(nodes)
        if n["kind"] == "label" and str((n["args"] or [""])[0]).startswith("🗓️ POOL")
    )
    pool = next(n for n in nodes[start:] if n["kind"] == "table")
    six_dates = ["04-10-2026"] if has_six else []
    five_dates = six_dates + ["03-10-2026"]
    four_dates = five_dates + ["02-10-2026"]
    assert [row["draw"] for row in pool["kwargs"]["rows"]] == four_dates
    toggle = next(n for n in nodes[start:] if n["kind"] == "toggle")
    assert toggle["args"][0] == ({4: "≥4", 5: "≥5", 6: "≥6"} if has_six else {4: "≥4", 5: "≥5"})
    if has_six:
        toggle["kwargs"]["on_change"](SimpleNamespace(value=6))
        assert [row["draw"] for row in pool["attrs"]["rows"]] == six_dates
    toggle["kwargs"]["on_change"](SimpleNamespace(value=5))
    assert [row["draw"] for row in pool["attrs"]["rows"]] == five_dates
    toggle["kwargs"]["on_change"](SimpleNamespace(value=4))
    assert [row["draw"] for row in pool["attrs"]["rows"]] == four_dates
    assert pool["attrs"]["pagination"]["page"] == 1
    assert "≥3 în pool" not in ui.text() and "⭐" not in ui.text()
    assert ui_hits._bench_target() == bench_target


def test_no_four_plus_hit_has_an_explicit_empty_message():
    with capture_ui() as ui:
        ui_hits._render_hits_4plus(
            entries("6/49", (3, 2, 1)), "6/49", {"pool_size": 16}
        )
    assert "Pool-ul n-a prins ≥4 în istoricul walk-forward." in ui.text()
    summary = next(n for n in ui.walk() if n["kind"] == "table")
    assert all(r["pool"] == "0 (0.00%)" for r in summary["kwargs"]["rows"])
