"""Budget recommendation controls and the information printed on each ticket."""
import asyncio
from unittest.mock import MagicMock

import pytest
from nicegui import Client, ui

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


def test_game_objective_and_method_can_be_set_before_first_generation(monkeypatch):
    view = _widgets(monkeypatch)
    inputs, io_bound = MagicMock(), MagicMock()
    monkeypatch.setattr(budget.run, "io_bound", io_bound)
    budget.lr_budget_panel(MagicMock(value=200), inputs)
    game, objective = view.select.return_value, view.toggle.return_value
    inverse = view.switch_by_label["Metoda inversă"]
    reduced = view.switch_by_label["Pool restrâns între 10 și 40"]
    panel = view.expansion.return_value

    async def scenario():
        for control, value in ((game, "6/49"), (objective, budget.ANY_PRIZE), (inverse, True), (reduced, True)):
            control.value = value
            await control.on_value_change.call_args.args[0]()
        assert panel.value is False
        inputs.assert_not_called()
        io_bound.assert_not_called()
        panel.set_value.assert_not_called()
        await view.button.return_value.on_click.call_args.args[0]()
        panel.set_value.assert_called_once_with(True)

    asyncio.run(scenario())


def test_panel_restores_preferences_and_saves_each_change_before_calculating(monkeypatch):
    view = _widgets(monkeypatch)
    methods, games, objectives, inputs = MagicMock(), MagicMock(), MagicMock(), MagicMock()
    budget.lr_budget_panel(
        MagicMock(value=200), inputs,
        initial_inverse=True, initial_restricted=True, on_method_change=methods,
        initial_game="joker", on_game_change=games,
        initial_objective=budget.ANY_PRIZE, on_objective_change=objectives,
    )
    inverse = view.switch_by_label["Metoda inversă"]
    restricted = view.switch_by_label["Pool restrâns între 10 și 40"]
    game = view.select.return_value
    objective = view.toggle.return_value
    assert (inverse.value, restricted.value, game.value) == (True, True, "joker")
    assert objective.value == budget.ANY_PRIZE
    objectives.assert_not_called()
    methods.assert_not_called()
    games.assert_not_called()

    async def scenario():
        for control, expected in [(inverse, (False, True)), (restricted, (False, False))]:
            control.value = False
            pending = control.on_value_change.call_args.args[0]()
            methods.assert_called_with(*expected)
            await pending
        game.value = "all"
        pending = game.on_value_change.call_args.args[0]()
        games.assert_called_once_with("all")
        await pending
        objective.value = budget.FOUR_PLUS
        pending = objective.on_value_change.call_args.args[0]()
        objectives.assert_called_once_with(budget.FOUR_PLUS)
        await pending
        assert methods.call_count == 2
        inputs.assert_not_called()

    asyncio.run(scenario())


def test_unknown_saved_game_uses_all_games(monkeypatch):
    view = _widgets(monkeypatch)
    budget.lr_budget_panel(MagicMock(value=200), MagicMock(), initial_game="absent")
    assert view.select.return_value.value == "all"


def test_interval_odds_follow_the_selected_game_in_both_modes_before_generation(monkeypatch):
    inputs, io_bound = MagicMock(), MagicMock()
    monkeypatch.setattr(budget.run, "io_bound", io_bound)
    heading = "Șansa ca toate numerele principale extrase să fie între 10 și 40 inclusiv"
    rates = {
        "6/49": "Loto 6/49 (6 numere) · 5,27%",
        "5/40": "Loto 5/40 (6 numere) · 19,18%",
        "joker": "Joker (5 numere din prima urnă) · 13,91%",
    }

    async def scenario():
        client = Client(ui.page("/test-lr-budget"))
        try:
            with client, ui.column() as host:
                budget.lr_budget_panel(MagicMock(value=200), inputs)
            game = next(node for node in host.descendants() if isinstance(node, ui.select))
            inverse = next(
                node for node in host.descendants()
                if isinstance(node, ui.switch) and node.text == "Metoda inversă"
            )
            reduced = next(
                node for node in host.descendants()
                if isinstance(node, ui.switch) and node.text == "Pool restrâns între 10 și 40"
            )
            odds = {
                node.text: node for node in host.descendants()
                if isinstance(node, ui.label) and node.text in {heading, *rates.values()}
            }

            def visible_odds():
                return {
                    text for text, node in odds.items()
                    if node.visible and all(parent.visible for parent in node.ancestors())
                }

            assert set(odds) == {heading, *rates.values()}
            assert inverse.value is False
            assert reduced.value is False
            assert reduced.enabled and reduced.visible
            assert all(parent.visible for parent in reduced.ancestors())
            assert visible_odds() == {heading, *rates.values()}
            reduced.value = True
            for enabled in (True, False):
                inverse.value = enabled
                assert reduced.value is True
                assert reduced.enabled and reduced.visible
                for game_id, rate in rates.items():
                    game.value = game_id
                    assert visible_odds() == {heading, rate}
                game.value = "all"
                assert visible_odds() == {heading, *rates.values()}
            game.value = "joker"
            inverse.value = False
            assert visible_odds() == {heading, rates["joker"]}
            await asyncio.sleep(0)
            inputs.assert_not_called()
            io_bound.assert_not_called()
        finally:
            client.delete()

    asyncio.run(scenario())


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
            if function is budget._budget_input_snapshot:
                return "unchanged", amount(objective)
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
        filtered = view.switch_by_label["Pool restrâns între 10 și 40"]
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
        assert filtered.value is True
        assert calls[-1] == (200, False, (10, 40))
        filtered.value = False
        await filtered.on_value_change.call_args.args[0]()
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

        async def io_bound(function, amount, goal, game=None, *args):
            if function is budget._budget_input_snapshot:
                return "unchanged", amount(goal)
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
    async def io_bound(fn, amount, goal, games=None, inputs=None, inverse=None):
        if fn is budget._budget_input_snapshot:
            return "unchanged", amount(goal)
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
        assert assigned == [{"6/49": None, "5/40": None, "joker": None}]
        first_actions = MagicMock(is_deleted=False)
        latest_actions = MagicMock(is_deleted=False)
        callbacks["6/49"]("6/49", "auto", first_actions)
        first_button = view.button.return_value
        first_actions.__enter__.assert_called_once()
        callbacks["5/40"]("5/40", "10–40", latest_actions)
        latest_button = view.button.return_value
        first_button.delete.assert_called_once()
        latest_actions.__enter__.assert_called_once()
        latest_button.delete.assert_not_called()
        assert "5/40" in view.label.call_args.args[0]
        assert len(assigned) == 1
        await view.button.call_args.kwargs["on_click"]()
        latest_button.delete.assert_called_once()
        assert len(analysed) == 1
        assert assigned[-1] == {"6/49": (10, 40), "5/40": (10, 40), "joker": None}
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


def test_prospective_registration_requires_an_explicit_generate_click(monkeypatch):
    view = _widgets(monkeypatch)
    slider = MagicMock(value=200)
    ledger_writes = []
    rendered = []
    monkeypatch.setattr(budget, "_render_recommendations", lambda *args: rendered.append(args))
    monkeypatch.setattr(budget, "_range_comparison", lambda *args, **kwargs: None)

    async def io_bound(function, *args, **kwargs):
        if function is budget._budget_input_snapshot:
            return ("same input", {})
        if function is budget._compare_games:
            return [{"game": "6/49", "ranges": [], "reason": "Fixture"}]
        if function is budget._generate_selection:
            return [{"game": "6/49"}]
        assert function is budget._register_live_plans
        ledger_writes.append(args)
        return ([{"game": "6/49", "target_day": "2026-11-26",
                  "captured_at": "2026-11-25T14:00:00+02:00"}], {})

    monkeypatch.setattr(budget.run, "io_bound", io_bound)

    async def exercise():
        budget.lr_budget_panel(slider, lambda _: {}, initial_game="6/49")
        panel, button = view.expansion.return_value, view.button.return_value
        panel.value = True
        await panel.on_value_change.call_args.args[0]()
        assert len(rendered) == 1 and not ledger_writes
        await button.on_click.call_args.args[0]()
        assert len(rendered) == 2
        assert ledger_writes == [([{"game": "6/49"}], {})]
        assert any("26.11.2026" in call.args[0] for call in view.label.call_args_list if call.args)
        slider.value = 170
        await slider.on_value_change.call_args.args[0]()
        assert len(rendered) == 3 and len(ledger_writes) == 1

    asyncio.run(exercise())



def test_fixed_share_policy_is_forwarded_only_when_selected(monkeypatch):
    view = _widgets(monkeypatch)
    captured = []
    compared = []

    async def io_bound(fn, *args, **kwargs):
        if fn is budget._budget_input_snapshot:
            return "fixed-history", {}
        if fn is budget._compare_games:
            compared.append(args[3].get("selection_policy"))
            return [{"game": "6/49", "recommended_range": None}]
        assert fn is budget._generate_selection
        captured.append(kwargs)
        return []

    monkeypatch.setattr(budget.run, "io_bound", io_bound)
    monkeypatch.setattr(budget, "_range_comparison", lambda *args, **kwargs: None)
    monkeypatch.setattr(budget, "_render_recommendations", lambda *args: None)
    choice = MagicMock()
    refresh = budget.lr_budget_panel(
        MagicMock(value=100), lambda game: {}, initial_game="6/49", on_fixed_share_change=choice,
    )
    toggle = view.switch_by_label["Fixed Share pe bilete"]
    assert toggle.value is False

    async def scenario():
        view.expansion.return_value.value = True
        await refresh()
        toggle.value = True
        await toggle.on_value_change.call_args.args[0]()
        toggle.value = False
        await toggle.on_value_change.call_args.args[0]()

    asyncio.run(scenario())
    assert captured == [{}, {"selection_policy": budget.ONLINE_TICKET_EXPERTS}, {}]
    assert compared == [None, budget.ONLINE_TICKET_EXPERTS, None]
    assert [call.args for call in choice.call_args_list] == [(True,), (False,)]


def test_540_inverse_200_lei_shows_exact_capacity_in_generation_comparison_and_validation(monkeypatch):
    import numpy as np
    import pandas as pd
    from loto_enterprise.core.lr_budget_forecast import recommend_playable_games
    from loto_enterprise.core.lr_range_analysis import compare_budget_ranges
    from loto_enterprise.core.lr_walk_forward import validate_lr_walk_forward

    rng = np.random.default_rng(540)
    matrix = [rng.choice(range(1, 41), 6, replace=False) for _ in range(253)]
    frame = pd.DataFrame(matrix, columns=[f"n{i}" for i in range(1, 7)])
    frame["date"] = pd.date_range("2020-01-01", periods=len(frame)).strftime("%d-%m-%Y")
    source = ("5-40.csv", frame)
    histories = {"5/40": source}
    reason = "În intervalul 10–40 rămân 15 numere eligibile după excluderea pool-ului normal (31 − 16 = 15). Cod 37 necesită 16."
    args = 200, budget.FOUR_PLUS, "5/40", histories
    for policy in (None, "online_ticket_experts"):
        plan, = recommend_playable_games(*args, inverse=True, pool_codes={}, number_range=(10, 40),
                                         selection_policy=policy)
        assert not plan["tickets"] and not plan["variants"] and not plan["price_bani"]
        assert plan["forecast_error"] == f"5-40.csv · {reason}"
    view = _widgets(monkeypatch)
    budget._render_recommendations([plan], 200, budget.FOUR_PLUS)
    view.label.assert_any_call(plan["forecast_error"])
    view.table.assert_not_called()
    comparison = compare_budget_ranges(*args, inverse=True)
    assert comparison["reason"] == reason
    budget._range_comparison(comparison, "Loto 5/40", "10–40", lambda *_: None, True, expanded=True)
    view.label.assert_any_call(reason)
    for replay in ("fixed", "daily"):
        with pytest.raises(ValueError) as error:
            validate_lr_walk_forward("5/40", source, budget_lei=200, objective=budget.FOUR_PLUS, scheme=37,
                                     inverse=True, restricted=True, dynamic=True, lookback=100, hit_target=4,
                                     joker_size=5, depth=10, replay_policy=replay)
        assert str(error.value) == reason


def test_panel_retries_a_provisional_range_without_restarting(monkeypatch):
    view = _widgets(monkeypatch)
    analyses = 0
    monkeypatch.setattr(budget, "_range_comparison", lambda *a, **kw: None)
    monkeypatch.setattr(budget, "_render_recommendations", lambda *a: None)

    async def io_bound(fn, *args, **kwargs):
        nonlocal analyses
        if fn is budget._budget_input_snapshot:
            return "same-inputs", {}
        if fn is budget._compare_games:
            analyses += 1
            return [{"game": game, "recommended_range": None,
                     "portfolio_refinement": {"complete": analyses > 1,
                                              "issues": [] if analyses > 1 else ["refinement_error"],
                                              "recipe_sha256": []}}
                    for game in args[2]]
        assert fn is budget._generate_selection
        return []

    monkeypatch.setattr(budget.run, "io_bound", io_bound)

    async def scenario():
        refresh = budget.lr_budget_panel(MagicMock(value=200), lambda game: {})
        view.expansion.return_value.value = True
        await refresh()
        toggle = view.switch_by_label["Pool restrâns între 10 și 40"]
        recalculate = toggle.on_value_change.call_args.args[0]
        for value in (True, False):
            toggle.value = value
            await recalculate()
        assert analyses == 2

    asyncio.run(scenario())
