"""Re-Bench preia automat sursele lipsă, păstrând datele existente."""
import json
import pandas as pd
import pytest
import app_nicegui as app


@pytest.fixture
def session(monkeypatch, tmp_path):
    games = [app._LOT.require_lottery(gid, app._LOT.RO) for gid in ("6/49", "joker", "5/40")]
    original = pd.DataFrame({"date": ["29-10-2026"], **{f"n{i}": [i] for i in range(1, 7)}})
    for game in games:
        path = tmp_path / game.csv
        path.parent.mkdir(parents=True, exist_ok=True)
        original.to_csv(path, index=False)
    initial = original.copy()
    initial.loc[0, "n1"] = 9  # distinctive uploaded snapshot
    filename = "custom_649.csv"
    monkeypatch.setattr(app, "PROJECT_ROOT", tmp_path)
    for key, value in {"datasets": [(filename, initial)], "dataset_game": {filename: "6/49"},
                       "dataset_loaded_at": {}, "wf_running": False, "virtual_wf_running": False,
                       "active_job_id": None, "bench_was_running": False, "bench_cancelled": False,
                       "result_sources": {filename: initial}, "results_recovered": "job #28"}.items():
        monkeypatch.setitem(app.STATE, key, value)
    for key, value in {"country_val": "RO", "games_val": ["6/49", "joker", "5/40"],
                       "pool_size_649_val": 16, "pool_size_joker_val": 12,
                       "pool_size_540_val": 19, "guarantee_val": 6, "max_variants_val": 0,
                       "autopilot_after_bench": False}.items():
        monkeypatch.setitem(app.SETTINGS, key, value)
    monkeypatch.setattr(app, "_bench_running", lambda: False)
    monkeypatch.setattr(app, "_istoric_has_data", lambda: True)
    monkeypatch.setattr(app.ui, "notify", lambda *_a, **_kw: None)
    monkeypatch.setattr(app, "_decision_target_mismatch", lambda: False)
    monkeypatch.setattr(app._bench_freshness_panel, "refresh", lambda: None)
    return games, initial


def test_rebench_loads_new_selected_games_before_launch_and_uses_their_pool_sizes(session, monkeypatch):
    games, initial = session
    started, history = [], []
    monkeypatch.setattr(app, "_launch_bench", lambda *_: started.append(json.loads(app._build_config_json())))
    monkeypatch.setattr(app, "_start_virtual_history", lambda **_: history.append(json.loads(app._build_config_json())) or True)
    app.run_rebench()
    assert len(started) == 1
    assert [g.game_id for g in games] == [d["tasks"][0]["game_label"] for d in started[0]["datasets"]]
    assert app.STATE["datasets"][0][1] is initial
    assert app.STATE["result_sources"]["custom_649.csv"] is initial
    app._on_bench_finished()
    assert len(history) == 1
    assert {d["tasks"][0]["game_label"]: d["tasks"][0]["pool_size"] for d in history[0]["datasets"]} == {"6/49": 16, "joker": 12, "5/40": 19}


def test_game_added_during_bench_is_loaded_after_completion_without_replacing_sources(session, monkeypatch):
    _games, initial = session
    monkeypatch.setitem(app.SETTINGS, "games_val", ["6/49"])
    monkeypatch.setattr(app, "_launch_bench", lambda *_: None)
    app.run_rebench()
    assert len(app.STATE["datasets"]) == 1
    monkeypatch.setitem(app.SETTINGS, "games_val", ["6/49", "joker"])
    captures = []
    monkeypatch.setattr(app, "_start_virtual_history", lambda **_: captures.append(json.loads(app._build_config_json())) or True)
    app._on_bench_finished()
    assert {d["tasks"][0]["game_label"] for d in captures[0]["datasets"]} == {"6/49", "joker"}
    assert app.STATE["datasets"][0][1] is initial
    assert app.STATE["result_sources"]["custom_649.csv"] is initial


def test_delayed_history_includes_newly_selected_sources_without_modifying_active_job(session, monkeypatch):
    _games, initial = session
    monkeypatch.setitem(app.STATE, "active_job_id", 28)
    monkeypatch.setattr(app, "_virtual_decision_fingerprint", lambda *_: (4, "unchanged"))
    app._on_bench_finished()
    config, _countries, _decision = app.STATE["_bench_history_pending"]
    tasks = [ds["tasks"][0] for ds in json.loads(config)["datasets"]]
    assert {task["game_label"]: task["pool_size"] for task in tasks} == {"6/49": 16, "joker": 12, "5/40": 19}
    assert app.STATE["result_sources"]["custom_649.csv"] is initial
    assert app.STATE["datasets"][0][1] is initial
    monkeypatch.setitem(app.STATE, "active_job_id", None)
    monkeypatch.setitem(app.STATE, "_autopilot_after_virtual", None)
    app._VIRTUAL_WF_CANCEL_REQUESTED.clear()
    starts = []
    monkeypatch.setattr(app, "_on_bench_finished", lambda: starts.append(True))
    app._continue_after_automatic_history(False)
    assert starts == [True]


def test_existing_filename_bound_to_other_game_is_not_replaced(session, monkeypatch):
    games, initial = session
    joker = games[1]
    name = __import__("pathlib").Path(joker.csv).name
    monkeypatch.setitem(app.STATE, "datasets", [(name, initial)])
    monkeypatch.setitem(app.STATE, "dataset_game", {name: "6/49"})
    monkeypatch.setitem(app.SETTINGS, "games_val", ["joker"])
    notices = []
    monkeypatch.setattr(app.ui, "notify", lambda msg, **_: notices.append(msg))
    app._load_missing_bench_histories()
    assert app.STATE["datasets"] == [(name, initial)]
    assert app.STATE["dataset_game"][name] == "6/49"
    assert any("deja folosit" in msg for msg in notices)


def test_no_sources_are_reloaded_while_bench_is_still_running(session, monkeypatch):
    monkeypatch.setattr(app, "_bench_running", lambda: True)
    monkeypatch.setattr(app, "_load_registry_histories", lambda *_: pytest.fail("Re-Bench active"))
    monkeypatch.setattr(app, "_launch_bench", lambda *_: pytest.fail("duplicate Re-Bench"))
    app.run_rebench()


def test_rebench_with_empty_session_loads_selected_histories_automatically(session, monkeypatch):
    monkeypatch.setitem(app.STATE, "datasets", [])
    monkeypatch.setitem(app.STATE, "dataset_game", {})
    launches = []
    monkeypatch.setattr(app, "_launch_bench", lambda *_: launches.append(json.loads(app._build_config_json())))
    app.run_rebench()
    assert len(launches) == 1
    assert {ds["tasks"][0]["game_label"] for ds in launches[0]["datasets"]} == {"6/49", "joker", "5/40"}
