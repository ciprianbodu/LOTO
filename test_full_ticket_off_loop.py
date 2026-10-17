"""„🎟️ Bilet complet” nu calculează biletele pe bucla NiceGUI.

La pool 16 (6/49 + Joker + 5/40, garanția 4) primul clic costă ~14 s de
recoacere și enumerare exactă. Un handler sincron le rula pe bucla de
evenimente: temporizatorul de 1 s, progresul WF și celelalte taburi stăteau.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest
from nicegui import core, ui
from nicegui.client import Client

import app_nicegui as app  # noqa: F401  leagă numele UI ca în producție
import ui_results
from loto_enterprise.core import full_ticket


@pytest.fixture
def page(monkeypatch):
    loop = asyncio.new_event_loop()
    monkeypatch.setattr(core, "loop", loop)
    client = Client(ui.page("/full-ticket-off-loop"))
    yield loop, client
    client.delete()
    pending = asyncio.all_tasks(loop)
    for task in pending:
        task.cancel()
    if pending:
        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
    loop.close()


def test_full_ticket_is_built_off_the_event_loop(page, monkeypatch):
    loop, client = page
    on_loop: list[bool] = []
    real = full_ticket.build_full_ticket

    def spy(game, data, tickets=1, **kw):
        try:
            asyncio.get_running_loop()
            on_loop.append(True)
        except RuntimeError:
            on_loop.append(False)
        return real(game, data, tickets, **kw)

    monkeypatch.setattr(full_ticket, "build_full_ticket", spy)
    data = {"hard_core": [3, 9, 14, 21, 27, 33, 38, 44], "guarantee": 3}
    monkeypatch.setitem(ui_results.STATE, "results", ([("f", {"6/49": data})], None))
    monkeypatch.setitem(ui_results.SETTINGS, "full_ticket_count_val", 1)

    async def click():
        # Exact ca NiceGUI pentru on_click=lambda: _show_full_ticket(): apel în
        # buclă, iar un rezultat awaitable e așteptat ca sarcină.
        with client:
            result = ui_results._show_full_ticket()
            if inspect.isawaitable(result):
                await result

    loop.run_until_complete(click())
    assert on_loop == [False], "build_full_ticket a rulat pe bucla de evenimente"
    texts = [e.text for e in client.elements.values() if isinstance(e, ui.label)]
    assert sum(t.startswith("V") and ": " in t for t in texts) == 3
