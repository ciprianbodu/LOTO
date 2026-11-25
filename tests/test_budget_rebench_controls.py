"""Budget changes must update the actual job configuration, even before a new pool exists."""
import asyncio
import json

import pandas as pd
import pytest

import app_nicegui as app
from covering.lr_budget import FOUR_PLUS, recommend_games


def test_budget_changes_update_the_next_lottery_job(monkeypatch):
    for key in ("country_val", "games_val", "lr_budget_lei", *app._POOL_KEYS.values(), *app._LR_CODE_KEYS.values()):
        monkeypatch.setitem(app.SETTINGS, key, app.DEFAULTS[key])
    frame = pd.DataFrame({"date": ["19-11-2026"], **{f"n{i}": [i] for i in range(1, 7)}})
    monkeypatch.setitem(app.STATE, "datasets", [("loto_6_49.csv", frame)])
    saves = []
    monkeypatch.setattr(app, "_save_settings", lambda: saves.append(app.SETTINGS["lr_budget_lei"]))
    for amount, code, width in ((200, 57, 12), (100, 48, 9), (200, 57, 12)):
        plans = recommend_games(amount, FOUR_PLUS, "6/49")
        app._apply_budget_plan(plans)
        app._apply_budget_plan(plans)
        config = json.loads(app._build_config_json())
        task, = config["datasets"][0]["tasks"]
        assert (task["lr_scheme_code"], task["pool_size"]) == (code, width)
        assert (app._lr_scheme_for("6/49").code, app.SETTINGS["lr_budget_lei"]) == (code, amount)
    assert saves == [200, 100, 200]


@pytest.mark.parametrize(("inverse", "restricted"), [(False, False), (True, False), (False, True), (True, True)])
def test_rebench_click_uses_selected_game_budget_and_both_switches(monkeypatch, inverse, restricted):
    choices = dict(lr_budget_game="6/49", lr_budget_objective=FOUR_PLUS,
                   lr_inverse_val=inverse, lr_restricted_val=restricted)
    dates = pd.DataFrame({"date": ["19-11-2026"], **{f"n{i}": [i] for i in range(1, 7)}})
    monkeypatch.setitem(app.STATE, "datasets", [("loto_6_49.csv", dates)])
    monkeypatch.setattr(app, "_selected_country", lambda: app._LOT.RO)
    monkeypatch.setattr(app, "_bench_running", lambda: False)
    monkeypatch.setattr(app, "lr_rebench_running", lambda: False)
    monkeypatch.setattr(app, "_load_missing_bench_histories", lambda games: None)
    calls = []
    monkeypatch.setattr(app, "run_rebench", lambda: calls.append("standard"))

    async def record(jobs):
        calls.extend(jobs)

    monkeypatch.setattr(app, "run_lr_rebench", record)
    asyncio.run(app._run_selected_rebench(choices, 171, lambda: calls.append("scoped")))
    if not (inverse or restricted):
        assert calls == ["standard"]
        return
    shown, (game, (name, history), settings) = calls
    assert (shown, game, name) == ("scoped", "6/49", "loto_6_49.csv")
    assert settings["inverse"] is inverse and settings["restricted"] is restricted
    assert settings["scheme"] == 49
    dates.loc[0, "n1"] = 44
    assert history.loc[0, "n1"] == 1


def test_scoped_rebench_respects_client_game_selection_and_keeps_missing_game(monkeypatch):
    choices = dict(lr_budget_game="all", lr_budget_objective=FOUR_PLUS,
                   lr_inverse_val=True, lr_restricted_val=True)
    monkeypatch.setitem(app.STATE, "datasets", [("joker.csv", pd.DataFrame({"joker": [1]}))])
    monkeypatch.setattr(app, "_load_missing_bench_histories", lambda games: None)
    jobs = app._lr_rebench_jobs(choices, 171)
    sources = {game: history for game, history, _ in jobs}
    assert sources.keys() == {"joker", "6/49", "5/40"}
    assert sources["6/49"] is None and sources["5/40"] is None
    assert sources["joker"][0] == "joker.csv"
