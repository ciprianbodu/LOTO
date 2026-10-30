"""Full Romanian number pools survive settings, submission and worker limits."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pandas as pd
import pytest

import app_nicegui as app
import ui_runtime
import worker
from loto_enterprise.core import lotteries as L


RO_MAX = {"6/49": 49, "5/40": 40, "joker": 45}
RO_DEFAULT = {"6/49": 16, "5/40": 19, "joker": 15}


def _settings(monkeypatch, **values):
    current = {**ui_runtime.DEFAULTS, **values}
    monkeypatch.setattr(ui_runtime, "SETTINGS", current)
    monkeypatch.setattr(app, "SETTINGS", current)
    return current


def test_registry_separates_romanian_universes_from_unchanged_defaults():
    for game, maximum in RO_MAX.items():
        lot = L.GAMES_BY_ID[game]
        assert lot.max_pool == maximum == lot.max_n
        assert lot.default_pool == RO_DEFAULT[game]
        assert ui_runtime.DEFAULTS[ui_runtime._POOL_KEYS[game]] == RO_DEFAULT[game]
    for lot in L.GAMES:
        if not lot.is_romanian:
            assert lot.max_pool == 16 and lot.default_pool == 10


@pytest.mark.parametrize("game", RO_MAX)
def test_all_accepted_values_reach_the_worker_without_a_hidden_old_limit(
    monkeypatch, game
):
    settings = _settings(monkeypatch)
    key = ui_runtime._POOL_KEYS[game]
    lot = L.GAMES_BY_ID[game]
    for requested in range(6, RO_MAX[game] + 1):
        settings[key] = requested
        from_ui = ui_runtime._pool_size_for(game)
        normalized = worker._task_norm(
            {"pool_size": from_ui, "max_variants": 123, "guarantee": 4}, lot
        )
        assert from_ui == normalized["pool_size"] == requested
        assert normalized["max_variants"] == 123
    assert worker._task_norm({"pool_size": 999}, lot)["pool_size"] == RO_MAX[game]


def test_settings_roundtrip_keeps_large_choices_and_existing_small_choices(
    tmp_path, monkeypatch
):
    settings = _settings(monkeypatch)
    saved = {ui_runtime._POOL_KEYS[g]: maximum for g, maximum in RO_MAX.items()}
    state = tmp_path / "ui-state.json"
    state.write_text(json.dumps(saved), encoding="utf-8")
    monkeypatch.setattr(ui_runtime, "UI_STATE_FILE", state)
    ui_runtime._load_settings()
    assert {g: ui_runtime._pool_size_for(g) for g in RO_MAX} == RO_MAX
    ui_runtime._save_settings()
    assert all(json.loads(state.read_text())[key] == value for key, value in saved.items())
    settings[ui_runtime._POOL_KEYS["6/49"]] = 12
    settings[ui_runtime._POOL_KEYS["5/40"]] = 17
    settings[ui_runtime._POOL_KEYS["joker"]] = 14
    ui_runtime._save_settings()
    ui_runtime._load_settings()
    assert {g: ui_runtime._pool_size_for(g) for g in RO_MAX} == {
        "6/49": 12, "5/40": 17, "joker": 14,
    }


@pytest.mark.parametrize("invalid", [None, "invalid", float("inf")])
def test_corrupt_saved_pool_uses_the_old_default_not_the_new_maximum(
    tmp_path, monkeypatch, invalid
):
    _settings(monkeypatch)
    state = tmp_path / "state.json"
    state.write_text(
        json.dumps({key: invalid for key in ui_runtime._POOL_KEYS.values()}),
        encoding="utf-8",
    )
    monkeypatch.setattr(ui_runtime, "UI_STATE_FILE", state)
    ui_runtime._load_settings()
    assert {g: ui_runtime._pool_size_for(g) for g in RO_MAX} == RO_DEFAULT


def test_config_contains_each_full_pool_and_keeps_the_requested_budget(monkeypatch):
    values = {ui_runtime._POOL_KEYS[g]: maximum for g, maximum in RO_MAX.items()}
    settings = _settings(monkeypatch, **values, max_variants_val=120)
    datasets = [
        (name, pd.read_csv(f"_ISTORIC/{name}").tail(3).reset_index(drop=True))
        for name in ("loto_6_49.csv", "loto_5_40.csv", "joker.csv")
    ]
    state = {**app.STATE, "datasets": datasets, "dataset_game": {}}
    monkeypatch.setattr(app, "STATE", state)
    monkeypatch.setattr(ui_runtime, "STATE", state)
    config = json.loads(app._build_config_json())
    tasks = [task for dataset in config["datasets"] for task in dataset["tasks"]]
    assert {task["game_label"]: task["pool_size"] for task in tasks} == RO_MAX
    assert all(task["max_variants"] == 120 for task in tasks)
    assert settings["max_variants_val"] == 120


def test_large_pool_explanations_are_conditional_and_do_not_change_settings(monkeypatch):
    settings = _settings(monkeypatch)
    assert ui_runtime._large_pool_notes() == []
    settings.update({ui_runtime._POOL_KEYS[g]: maximum for g, maximum in RO_MAX.items()})
    settings["max_variants_val"] = 100
    before = dict(settings)
    notes = ui_runtime._large_pool_notes()
    assert len(notes) == 3
    assert all("100 variante" in note and "catalogului LR" in note for note in notes)
    assert all("va fi relaxată" in note for note in notes)
    assert "800,00 lei" in notes[0]
    assert settings == before
    settings["max_variants_val"] = 0
    settings["guarantee_val"] = 6
    assert "13.983.816 variante" in ui_runtime._large_pool_notes()[0]
    assert "Stabilește un plafon pozitiv" in ui_runtime._large_pool_notes()[0]
    assert settings["max_variants_val"] == 0


def test_foreign_pool_limit_and_default_remain_unchanged(monkeypatch):
    settings = _settings(monkeypatch, country_val="BG", pool_size_val=49)
    assert ui_runtime._pool_size_for("bg_toto2") == 16
    assert ui_runtime._large_pool_notes() == []
    lot = L.GAMES_BY_ID["bg_toto2"]
    assert worker._task_norm({"pool_size": 49}, lot)["pool_size"] == 16
    assert settings["pool_size_val"] == 49  # Reading the setting never overwrites it.


def test_adaptive_history_accepts_each_games_real_maximum():
    for game, maximum in RO_MAX.items():
        assert maximum in app._adaptive_pool_range(f"{game}_{maximum}")
        assert maximum + 1 not in app._adaptive_pool_range(f"{game}_{maximum + 1}")
    lot = L.GAMES_BY_ID["de_lotto"]
    stem = lot.history_key_prefix + lot.geometry
    assert 16 in app._adaptive_pool_range(f"{stem}_16")
    assert 17 not in app._adaptive_pool_range(f"{stem}_17")


def test_skipped_large_pool_metadata_survives_storage_and_cache_only_recovery(monkeypatch):
    from loto_enterprise.core import walk_forward_adapter as wf

    state = {**app.STATE, "retro": {}, "retro_meta": {}, "results": ([], 1)}
    state["result_sources"] = {"history.csv": pd.DataFrame({"n1": [1]})}
    monkeypatch.setattr(app, "STATE", state)
    monkeypatch.setattr(app, "_iter_wf_jobs", lambda _bundle: [("history.csv", "6/49", {})])
    monkeypatch.setattr(app, "_echo_mismatch", lambda *args: False)
    monkeypatch.setattr(app, "_result_scorers_match_decision", lambda *args: True)
    monkeypatch.setattr(app, "_wf_run_kwargs", lambda *args: {})
    skip = {
        "large_pool_wf_skipped": True, "skip_reason": "row_limit",
        "estimated_rows": 2000000, "row_limit": 1500000, "pool_size": 49,
    }
    monkeypatch.setattr(wf, "run_honest_walk_forward", lambda **kwargs: ([], skip))
    assert app._load_cached_walk_forward() == 0
    assert state["retro"]["history.csv_6/49"] == []
    stored = state["retro_meta"]["history.csv_6/49"]
    assert all(stored[key] == value for key, value in skip.items())


def test_skipped_large_pool_wf_finishes_normally_without_a_retry_loop(monkeypatch):
    from loto_enterprise.core import walk_forward_adapter as wf

    state = {
        **app.STATE, "results": ([], 1), "retro": {}, "retro_meta": {},
        "result_sources": {"history.csv": pd.DataFrame({"n1": [1]})},
        "job_start_time": None,
    }
    monkeypatch.setattr(app, "STATE", state)
    monkeypatch.setattr(app, "_iter_wf_jobs", lambda _bundle: [("history.csv", "6/49", {})])
    monkeypatch.setattr(app, "_echo_mismatch", lambda *args: False)
    monkeypatch.setattr(app, "_decision_moved_since_generation", lambda *args: False)
    monkeypatch.setattr(app, "_wf_run_kwargs", lambda *args: {})
    called = []
    monkeypatch.setattr(app, "_save_report_file", lambda: called.append("report"))
    monkeypatch.setattr(app, "_finalize_pipeline", lambda: called.append("finalize"))

    class InlineThread:
        def __init__(self, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(app, "threading", SimpleNamespace(Thread=InlineThread))
    attempts = []

    def skip(**kwargs):
        attempts.append(kwargs)
        return [], {"large_pool_wf_skipped": True, "skip_reason": "variant_cap_required"}

    monkeypatch.setattr(wf, "run_honest_walk_forward", skip)
    app._start_walk_forward()
    assert len(attempts) == 1
    assert state["wf_running"] is False and state["wf_progress"] == 1.0
    assert state["retro_meta"]["history.csv_6/49"]["large_pool_wf_skipped"] is True
    assert called == ["report", "finalize"]


@pytest.mark.parametrize("game,pool,expected", [
    ("6/49", 16, "04db8a58098cbfb773677e5fd2c7f63ad677a50038bfef5f7e2a77882d86c523"),
    ("5/40", 19, "17c4f563f80a8f4970aa106b5437df9b541b516d7fadc003f8f6ea60d789ff12"),
    ("joker", 15, "e494e2e297af089c46270e19815852e609525fbb1e7771dd3d8d73303c04fc80"),
])
def test_small_pool_result_cache_keys_match_the_pre_extension_keys(
    tmp_path, monkeypatch, game, pool, expected
):
    from loto_enterprise.core import walk_forward_adapter as wf

    monkeypatch.setattr(worker, "PROJECT_ROOT", tmp_path)
    monkeypatch.delenv("LOTO_WHEEL_METHOD", raising=False)
    monkeypatch.setattr(wf, "_wheel_sig", lambda *args: "fixed-design-signature")
    config = {"datasets": [{"tasks": [{
        "game_label": game, "pool_size": pool, "guarantee": 4, "max_variants": 50,
    }]}]}
    key = worker._pipeline_cache_key("fixed-history", config)
    assert key == f"v22:fixed-history:wheel=auto:{expected}"


def test_large_pool_result_cache_has_its_own_namespace(tmp_path, monkeypatch):
    from loto_enterprise.core import walk_forward_adapter as wf

    monkeypatch.setattr(worker, "PROJECT_ROOT", tmp_path)
    monkeypatch.delenv("LOTO_WHEEL_METHOD", raising=False)
    monkeypatch.setattr(wf, "_wheel_sig", lambda *args: "fixed-design-signature")
    config = {"datasets": [{"tasks": [{
        "game_label": "6/49", "pool_size": 49, "guarantee": 4, "max_variants": 50,
    }]}]}
    key = worker._pipeline_cache_key("fixed-history", config)
    old = "v22:fixed-history:wheel=auto:e80ea7ae3d3a93d1b94aecc63156d7cdc03cffecf27c1901b98e98884e81c6de"
    assert key == old + ":lp1"


@pytest.mark.parametrize("game,old_max,new_size", [
    ("6/49", 16, 17), ("joker", 16, 17), ("5/40", 19, 20),
])
def test_worker_cache_namespace_starts_above_each_previous_game_limit(
    tmp_path, monkeypatch, game, old_max, new_size
):
    from loto_enterprise.core import walk_forward_adapter as wf

    monkeypatch.setattr(worker, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(wf, "_wheel_sig", lambda *args: "fixed-design-signature")
    config = {"datasets": [{"tasks": [{"game_label": game, "pool_size": old_max}]}]}
    assert not worker._pipeline_cache_key("same-history", config).endswith(":lp1")
    config["datasets"][0]["tasks"][0]["pool_size"] = new_size
    assert worker._pipeline_cache_key("same-history", config).endswith(":lp1")
