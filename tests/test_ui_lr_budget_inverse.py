"""Budget recommendation controls and the information printed on each ticket."""
import asyncio
from unittest.mock import MagicMock

import pytest

import ui_lr_budget as budget


def _widgets(monkeypatch):
    view = MagicMock()
    view.switch_by_label = {}

    def widget(*args, **kwargs):
        control = MagicMock()
        control.value = kwargs.get("value")
        control.is_deleted = False
        control.classes.return_value = control.props.return_value = control.tooltip.return_value = control
        control.__enter__.return_value = control
        return control

    for name in ("button", "switch", "expansion", "select", "toggle", "column", "row", "table", "label"):
        factory = getattr(view, name)
        factory.return_value = widget()
        def create(*args, _factory=factory, **kwargs):
            result = widget(*args, **kwargs)
            _factory.return_value = result
            if _factory is view.switch:
                view.switch_by_label[args[0]] = result
            return result
        factory.side_effect = create
    monkeypatch.setattr(budget, "ui", view)
    return view


def test_inverse_budget_supersedes_running_normal_forecast(monkeypatch):
    view = _widgets(monkeypatch)
    slider = MagicMock(value=180)
    rendered = []
    monkeypatch.setattr(budget, "_render_recommendations", lambda *args: rendered.append(args))

    async def scenario():
        calls, settings = [], []
        started, finish = asyncio.Event(), asyncio.Event()

        def apply_budget(plans):
            settings.extend(plans)

        async def calculate(function, amount, objective, game, *, inverse=None, inverse_range=None):
            if function is budget.recommend_games:
                return [(amount, objective, game)]
            assert function is budget.recommend_playable_games
            assert settings[-1] == (amount, objective, game)
            calls.append((amount, inverse, inverse_range))
            if len(calls) == 1:
                started.set()
                await finish.wait()
            return [(amount, inverse, inverse_range)]

        monkeypatch.setattr(budget.run, "io_bound", calculate)
        refresh = budget.lr_budget_panel(slider, lambda game: {}, on_budget_plan=apply_budget)
        switch = view.switch_by_label["Metoda inversă"]
        filtered = view.switch_by_label["Doar numere principale între 10 și 40"]
        filtered.bind_visibility_from.assert_called_once_with(switch, "value")
        filtered.bind_enabled_from.assert_called_once_with(switch, "value")
        assert filtered.value is False
        panel = view.expansion.return_value
        button = view.button.return_value
        view.button.assert_called_once_with("Generează recomandarea", icon="confirmation_number", color="positive")
        view.switch.assert_any_call("Metoda inversă", value=False)

        await button.on_click.call_args.args[0]()
        panel.set_value.assert_called_once_with(True)
        panel.value = True
        pending = asyncio.create_task(panel.on_value_change.call_args.args[0]())
        await asyncio.wait_for(started.wait(), timeout=2)
        switch.value = True
        await switch.on_value_change.call_args.args[0]()
        filtered.value = True
        await filtered.on_value_change.call_args.args[0]()
        slider.value = 200
        await slider.on_value_change.call_args.args[0]()
        assert not rendered
        assert "200 lei" in view.label.call_args.args[0]
        finish.set()
        await pending
        assert calls == [(180, False, None), (200, True, (10, 40))]
        assert rendered == [([(200, True, (10, 40))], 200, budget.FOUR_PLUS)]
        assert settings == [(180, budget.FOUR_PLUS, None), (200, budget.FOUR_PLUS, None)]

        await refresh()
        assert len(settings) == 2
        assert rendered[-1] == ([(200, True, (10, 40))], 200, budget.FOUR_PLUS)
        switch.value = False
        await switch.on_value_change.call_args.args[0]()
        assert calls[-1] == (200, False, None)
        assert len(settings) == 2

    asyncio.run(scenario())



def test_obsolete_or_closed_plan_does_not_update_benchmark_controls(monkeypatch):
    view = _widgets(monkeypatch)
    slider = MagicMock(value=200)
    applied, calculated = [], []
    monkeypatch.setattr(budget, "_render_recommendations", lambda *args: None)

    async def scenario():
        waiting, release = asyncio.Event(), asyncio.Event()

        async def io_bound(function, amount, goal, game, **kwargs):
            if function is budget.recommend_games:
                if amount == 200:
                    waiting.set()
                    await release.wait()
                return [amount]
            calculated.append(amount)
            return [amount]

        monkeypatch.setattr(budget.run, "io_bound", io_bound)
        refresh = budget.lr_budget_panel(slider, lambda game: {}, on_budget_plan=applied.extend)
        panel = view.expansion.return_value
        panel.value = True
        pending = asyncio.create_task(refresh())
        await asyncio.wait_for(waiting.wait(), timeout=2)
        slider.value = 180  # The UI event may still be queued when planning returns.
        release.set()
        await pending
        assert applied == calculated == [180]

        waiting.clear()
        release.clear()
        slider.value = 200
        pending = asyncio.create_task(refresh())
        await asyncio.wait_for(waiting.wait(), timeout=2)
        panel.value = False
        await refresh()
        release.set()
        await pending
        assert applied == calculated == [180]
        view.button.return_value.props.assert_called_with("aria-busy=false", remove="loading")

    asyncio.run(scenario())


@pytest.mark.parametrize(("limits", "remaining", "label"), [
    (None, 38, "38 de numere eligibile"),
    ((10, 40), 31, "31 de numere eligibile între 10 și 40 inclusiv"),
])
def test_inverse_ticket_shows_excluded_pools_eligible_counts_and_official_zone(monkeypatch, limits, remaining, label):
    view = _widgets(monkeypatch)
    budget._ticket_details({
        "game": "joker",
        "game_name": "Joker",
        "forecast": {
            "pool": [11, 12, 13, 14, 15, 16, 17], "generated": False, "code": 10,
            "excluded_pool": [1, 2, 3, 4, 5, 6, 7], "eligible_count": remaining, "inverse_range": limits,
            "excluded_jokers": [3, 7], "eligible_joker_count": 18, "jokers": [8],
            "selection": {"method": "frequency"}, "joker_selection": {}, "dynamic": False,
            "filename": "joker.csv", "date": None, "rows": 0,
        },
        "tickets": [[{"code": 10, "pool_size": 7, "numbers": [11, 12, 13, 14, 15, 16, 17], "joker": 8}]],
    })
    labels = [entry.args[0] for entry in view.label.call_args_list]
    assert "Pool normal exclus" in labels and "1, 2, 3, 4, 5, 6, 7" in labels
    assert f"Pool invers · 7 numere selectate din {label} · Cod 10" in labels
    assert "Urna 2 Joker · pool normal exclus · 18 numere eligibile după excludere" in labels
    assert "3, 7" in labels
    assert "Metoda pool-ului invers · frequency (Re-Bench)" in labels
    assert view.table.call_args.kwargs["rows"] == [{
        "id": "1-0", "ticket": 1, "zone": "A", "code": 10, "pool": 7,
        "numbers": "11, 12, 13, 14, 15, 16, 17", "joker": 8,
    }]


def test_unplayable_range_displays_reason_without_zone_numbers(monkeypatch):
    view = _widgets(monkeypatch)
    reason = "Rămân 10 numere eligibile între 10 și 40 inclusiv. Sunt necesare 12."
    budget._render_recommendations(
        [{"game": "6/49", "game_name": "Loto 6/49", "tickets": [], "forecast_error": reason}],
        180, budget.FOUR_PLUS,
    )
    view.label.assert_any_call(reason)
    view.table.assert_not_called()
