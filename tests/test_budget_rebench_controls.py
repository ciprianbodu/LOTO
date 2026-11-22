"""Budget changes must update the actual job configuration, even before a new pool exists."""
import json

import pandas as pd

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
