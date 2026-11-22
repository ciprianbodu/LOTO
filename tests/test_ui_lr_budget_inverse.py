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

        async def calculate(function, amount, objective, *args):
            if function is budget.recommend_games:
                return [(amount, objective, args[0])]
            if function is budget._compare_games:
                return [{"game": gid, "ranges": [], "reason": "Test"} for gid in args[0]]
            assert function is budget._generate_selection
            assert settings[-1] == (amount, objective, None)
            selections, _, inverse = args
            inverse_range = selections["6/49"]
            assert all(limit == inverse_range for limit in selections.values())
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

        async def io_bound(function, amount, goal, game, *args):
            if function is budget.recommend_games:
                if amount == 200:
                    waiting.set()
                    await release.wait()
                return [amount]
            if function is budget._compare_games:
                return [{"game": gid, "ranges": [], "reason": "Test"} for gid in game]
            calculated.append(amount)
            return [amount]

        monkeypatch.setattr(budget.run, "io_bound", io_bound)
        refresh = budget.lr_budget_panel(slider, lambda game: {}, on_budget_plan=applied.extend)
        panel = view.expansion.return_value
        button = view.button.return_value
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
        button.props.assert_called_with("aria-busy=false", remove="loading")

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

def test_automatic_and_manual_bases_per_game_reuse_analysis(monkeypatch):
    view = _widgets(monkeypatch)
    assigned = []
    analysed = []
    callbacks = {}
    def display(report, name, choice, on_change, forced, *, expanded):
        callbacks[report["game"]] = on_change
    monkeypatch.setattr(budget, "_range_comparison", display)
    monkeypatch.setattr(budget, "_render_recommendations", lambda *args: None)
    async def io_bound(fn, amount, goal, games, inputs, inverse):
        if fn is budget._compare_games:
            analysed.append(tuple(games))
            return [
                {"game": game, "recommended_range": (10, 40) if game == "6/49" else None}
                for game in games
            ]
        assert fn is budget._generate_selection
        assigned.append(dict(games))
        return []
    monkeypatch.setattr(budget.run, "io_bound", io_bound)
    async def scenario():
        refresh = budget.lr_budget_panel(MagicMock(value=200), lambda game: {})
        view.expansion.return_value.value = True
        await refresh()
        assert assigned == [{"6/49": (10, 40), "5/40": None, "joker": None}]
        callbacks["6/49"]("6/49","Baza completă")
        callbacks["5/40"]("5/40","10–40")
        assert len(assigned) == 1
        confirm = next(c.kwargs["on_click"] for c in view.button.call_args_list if c.args == ("Generează cu baza aleasă",))
        await confirm()
        assert len(analysed) == 1
        assert assigned[-1] == {"6/49": None, "5/40": (10, 40), "joker": None}
    asyncio.run(scenario())


def test_range_evidence_displays_number_and_calendar_gaps(monkeypatch):
    view = _widgets(monkeypatch)
    report = {
        "game": "6/49", "code": 57, "pool_size": 12, "recommended_range": None,
        "reason": "Date insuficiente pentru o bază restrânsă.", "metric": "4+ pe bilet",
        "ranges": [{
            "range": (10, 40), "eligible_now": 25, "feasible_now": True, "evaluated": 100,
            "pool_3plus": 17, "pool_4plus": 4, "ticket_3plus": 17, "ticket_4plus": 2,
            "recent_successes": 1, "recent_evaluated": 25, "infeasible": 0,
            "successes": 9, "objective_evaluated": 98, "objective_missing": 2,
            "phase": {"phase": "8–14 zile", "sufficient": False, "compared": 4},
            "chronology": {"window_draws": 100, "occurrences": 1, "last_date": "2026-11-12",
                           "draws_since": 2, "days_since": 7, "median_gap_draws": None, "median_gap_days": None},
        }],
        "first_date": "2025-11-01", "as_of": "2026-11-19", "rows": 100,
    }
    budget._range_comparison(report, "Loto 6/49", "auto", lambda *args: None, False, expanded=True)
    assert list(view.select.call_args.args[0]) == ["auto", "Baza completă", "10–40"]
    performance, chronology = [c.kwargs["rows"][0] for c in view.table.call_args_list]
    assert (performance["recent"], performance["ticket"], performance["eligible"]) == ("1 / 25 (4,0%)","17 / 2","25")
    assert performance["objective"] == "9 / 98 (9,2%) · 2 extrageri fără date complete"
    assert performance["phase"] == "8–14 zile · 4 comparații, date insuficiente"
    assert (chronology["events"],chronology["last"],chronology["wait"],chronology["median"]) == (
        "1 / 100","12.11.2026","2 / 7","N/D / N/D",
    )
    assert any("19.11.2026" in call.args[0] for call in view.label.call_args_list)
