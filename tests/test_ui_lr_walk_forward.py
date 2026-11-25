"""A saved LR validation keeps its own settings and paginates actual holdout draws."""
import asyncio
from random import Random

import pandas as pd
from nicegui import Client, ui

import ui_lr_walk_forward as panel
from covering.lr_budget import FOUR_PLUS
from loto_enterprise.core.lr_walk_forward import validate_lr_walk_forward


def test_render_saved_joker_validation_alongside_changed_selection(monkeypatch):
    rng = Random(409)
    history = pd.DataFrame([
        [day.strftime("%d-%m-%Y"), *rng.sample(range(1, 46), 5), rng.randrange(1, 21)]
        for day in pd.date_range("2026-01-01", periods=70)
    ], columns=["date", "n1", "n2", "n3", "n4", "n5", "joker"])
    settings = dict(scheme=24, inverse=True, restricted=True, dynamic=True, lookback=100, hit_target=4,
                    joker_size=2, objective=FOUR_PLUS, budget_lei=100, depth=30)
    result = validate_lr_walk_forward("joker", ("joker.csv", history), **settings)
    entry = dict(game="joker", name="Joker", source="joker.csv", settings=settings, result=result)
    report = dict(entries=[entry], finished_at="2026-11-25T13:15:27+02:00")
    monkeypatch.setattr(panel, "lr_walk_forward_snapshot",
                        lambda since, **_: (1, None, report, "") if since is None else None)
    monkeypatch.setattr(panel.ui, "timer", lambda *args: None)

    async def scenario():
        client = Client(ui.page("/test-ui-walk-forward"))
        try:
            with client:
                rendered = panel.lr_walk_forward_panel(lambda: "Loto 6/49 · Metoda normală", lambda: None)
                nodes = list(rendered.descendants())
                labels = [node.text for node in nodes if isinstance(node, ui.label)]
                assert "Loto 6/49 · Metoda normală" in labels
                assert any("Metoda inversă" in text and "30%" in text and "joker.csv" in text for text in labels)
                assert any("Cod LR 24" in text and "variante" in text and "cost total" in text for text in labels)
                assert any("5 numere din urna principală au fost între 10 și 40" in text for text in labels)
                assert any("Urna 2" in text and "în pool" in text and "pe aceeași variantă cu cel puțin un număr principal" in text for text in labels)
                summary = result["summary"]
                rate = f"{summary['fallback_draws']} din {summary['evaluated']} extrageri jucate"
                assert any("Metodă de rezervă" in text and rate in text for text in labels)
                table = next(node for node in nodes if isinstance(node, ui.table))
                assert len(table.rows) == 10
                assert {"excluded_pool", "excluded_jokers", "ticket_hits", "pool_hits", "ticket_joker_hit"} <= table.rows[0].keys()
                assert table.rows[0]["id"] == len(result["draws"]) - 1
                pager = next(node for node in nodes if isinstance(node, ui.pagination))
                pager.set_value(2)
                assert table.rows[0]["id"] == len(result["draws"]) - 11
        finally:
            client.delete()

    asyncio.run(scenario())
