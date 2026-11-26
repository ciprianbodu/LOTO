import json

import pandas as pd
import pytest

import app_nicegui as app
from covering.lr_budget import ANY_PRIZE, FOUR_PLUS, SINGLE_SCHEME_MODE, recommend_games
from loto_enterprise.core import lr_mail_forecast


@pytest.mark.parametrize("legacy", [True, False])
def test_result_email_uses_the_committed_job_budget_history_and_original_pool(monkeypatch, legacy):
    frame = pd.DataFrame({"date": ["19-11-2026"], **{f"n{i}": [i] for i in range(1, 7)}})
    fname = "loto_6_49.csv"
    monkeypatch.setitem(app.STATE, "datasets", [(fname, frame)])
    for key, value in {
        "country_val": "RO", "games_val": [], "mail_on_complete": True,
        "lr_budget_lei": 200, "lr_budget_objective": ANY_PRIZE, "bench_hit_target": 4,
    }.items():
        monkeypatch.setitem(app.SETTINGS, key, value)
    config = app._build_config_json()
    dataset, = json.loads(config)["datasets"]
    assert dataset["lr_mail_settings"] == {
        "lr_budget_lei": 200, "lr_budget_objective": FOUR_PLUS, "lr_budget_mode": SINGLE_SCHEME_MODE,
    }
    if legacy:
        old_job = json.loads(config)
        captured = old_job["datasets"][0]["lr_mail_settings"]
        captured.pop("lr_budget_mode")
        captured["lr_budget_objective"] = ANY_PRIZE
        config = json.dumps(old_job)
    task, = dataset["tasks"]
    assert {"lr_budget_lei", "lr_budget_objective", "lr_budget_mode"}.isdisjoint(task)
    sources = app._result_sources_from_job({"config_json": config})
    monkeypatch.setitem(app.SETTINGS, "lr_budget_lei", 100)
    monkeypatch.setitem(app.SETTINGS, "lr_budget_objective", FOUR_PLUS)
    monkeypatch.setitem(app.SETTINGS, "bench_hit_target", 3)
    pool = list(range(1, 13))
    primary = {"hard_core": pool.copy(), "dynamic_method": True, "lr_scheme_code": 57, "lookback": 100}
    snapshot = app._mail_snapshot_inputs(([(fname, {"6/49": primary})], 1), sources)
    sources[fname].loc[0, "n1"] = 44
    primary["hard_core"].clear()
    calls = []
    def prepare(record, source, **options):
        calls.append((options, source.n1.iloc[0], record["data"]["hard_core"]))
        return {"inverse_reduced": {"error": "verificare simulată"}}
    monkeypatch.setattr(lr_mail_forecast, "prepare_inverse_mail_pools", prepare)
    monkeypatch.setattr(app, "load_mail_config", lambda *_: {"mail_to": "local@example.invalid"})
    delivery = []
    monkeypatch.setattr(app, "send_email", lambda cfg, subject, body: delivery.append(body))
    app._maybe_send_results_email(snapshot)
    assert len(delivery) == 1 and "verificare simulată" in delivery[0]
    assert app._build_mail_body(snapshot) == delivery[0]
    assert len(calls) == 1
    options, first, original = calls[0]
    assert (options["budget_lei"], options["hit_target"]) == (200, 4)
    assert options["objective"] == (ANY_PRIZE if legacy else FOUR_PLUS)
    assert options.get("budget_mode") == (None if legacy else SINGLE_SCHEME_MODE)
    assert first == 1 and original == pool


def test_failed_inverse_keeps_original_and_other_games(monkeypatch):
    snapshot = {"games": [], "sources": {}}
    for game in ("6/49", "joker"):
        snapshot["games"].append({"game": game, "fname": game, "lr_mail_settings": {}})
    def per_game(record, source, **kwargs):
        if record["game"] == "6/49":
            raise ValueError("indisponibil")
        return {"inverse_reduced": {"pool": [10, 20, 30]}}
    monkeypatch.setattr(lr_mail_forecast, "prepare_inverse_mail_pools", per_game)
    app._prepare_mail_alternatives(snapshot)
    first, second = snapshot["games"]
    assert "error" in first["pool_alternatives"]["inverse_reduced"]
    assert second["pool_alternatives"]["inverse_reduced"]["pool"] == [10, 20, 30]


def test_new_mail_budget_uses_four_plus_after_a_legacy_any_prize_plan(monkeypatch):
    monkeypatch.setattr(app, "_save_settings", lambda: None)
    for key in ("lr_budget_lei", "lr_budget_objective", *app._POOL_KEYS.values(), *app._LR_CODE_KEYS.values()):
        monkeypatch.setitem(app.SETTINGS, key, app.DEFAULTS[key])
    app._apply_budget_plan(recommend_games(100, ANY_PRIZE, "6/49"))
    assert app._budget_objective() == FOUR_PLUS and app.SETTINGS["lr_budget_lei"] == 100
