"""Official Romanian LR controls and separate legacy worker pool limits."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pandas as pd
import pytest

import app_nicegui as app
import ui_runtime
import worker
from loto_enterprise.core import lotteries as L
from covering.lr_schemes import lr_schemes
from test_bench_status_ui import sidebar_context


RO_MAX = {"6/49": 49, "5/40": 40, "joker": 45}
RO_MIN = {"6/49": 9, "5/40": 7, "joker": 7}  # cel mai mic cod LR oficial
RO_DEFAULT = {"6/49": 9, "5/40": 8, "joker": 10}
RO_LR_MAX = {game: max(s.n for s in lr_schemes(game)) for game in RO_MAX}


def _settings(monkeypatch, **values):
    current = {**ui_runtime.DEFAULTS, **values}
    monkeypatch.setattr(ui_runtime, "SETTINGS", current)
    monkeypatch.setattr(app, "SETTINGS", current)
    return current


def test_registry_separates_romanian_universes_from_unchanged_defaults():
    for game, maximum in RO_MAX.items():
        lot = L.GAMES_BY_ID[game]
        assert lot.max_pool == maximum == lot.max_n
        assert lot.min_pool == RO_MIN[game]
        assert lot.default_pool == {"6/49": 16, "5/40": 19, "joker": 15}[game]
        assert ui_runtime.DEFAULTS[ui_runtime._POOL_KEYS[game]] == RO_DEFAULT[game]
    for lot in L.GAMES:
        if not lot.is_romanian:
            assert lot.max_pool == 16 and lot.min_pool == 6 and lot.default_pool == 10


def test_min_pool_is_the_smallest_official_lr_scheme_per_game():
    """Minimul UI (2026-11-11) = cel mai mic cod LR oficial din catalog."""
    from covering import lr_schemes as lr

    for game, minimum in RO_MIN.items():
        assert min(s.n for s in lr.lr_schemes(game)) == minimum


def test_startup_defaults_match_the_official_schemes_the_user_plays():
    from covering.lr_schemes import lr_schemes

    played = {"6/49": (48, 9, 12), "5/40": (16, 8, 21), "joker": (24, 10, 14)}
    for game, (code, pool, variants) in played.items():
        scheme = next(s for s in lr_schemes(game) if s.code == code)
        assert scheme.n == pool == ui_runtime.DEFAULTS[ui_runtime._POOL_KEYS[game]]
        assert scheme.size == variants


@pytest.mark.parametrize("game", RO_MAX)
def test_all_official_slider_positions_reach_the_worker_with_the_exact_code(
    sidebar_context, game
):
    _client, settings, _state = sidebar_context
    lot = L.GAMES_BY_ID[game]
    schemes = sorted(lr_schemes(game), key=lambda s: (s.n, s.size, s.code))
    slider = app._render_lr_pool_input(game, f"Pool {game}")
    try:
        assert slider._props["min"] == 0
        assert slider._props["max"] == len(schemes) - 1
        visited = []
        for index, scheme in enumerate(schemes):
            slider.set_value(index)
            assert settings[ui_runtime._LR_CODE_KEYS[game]] == scheme.code
            assert ui_runtime._pool_size_for(game) == scheme.n
            assert ui_runtime._lr_scheme_for(game) == scheme
            normalized = worker._task_norm(
                {"pool_size": scheme.n, "lr_scheme_code": scheme.code,
                 "max_variants": scheme.size, "guarantee": 4}, lot
            )
            assert normalized["pool_size"] == scheme.n
            assert normalized["lr_scheme_code"] == scheme.code
            assert normalized["max_variants"] == scheme.size
            visited.append((scheme.code, scheme.n, scheme.size))
        assert visited == [(s.code, s.n, s.size) for s in schemes]
        assert len({code for code, _, _ in visited}) == len(schemes)
    finally:
        slider.delete()


@pytest.mark.parametrize("game", RO_MAX)
def test_legacy_programmatic_worker_still_accepts_the_full_number_universe(game):
    """A task without an LR code retains the complete legacy API range."""
    lot = L.GAMES_BY_ID[game]
    for requested in range(RO_MIN[game], RO_MAX[game] + 1):
        normalized = worker._task_norm(
            {"pool_size": requested, "max_variants": 123, "guarantee": 4}, lot
        )
        assert normalized["pool_size"] == requested
        assert normalized["max_variants"] == 123
        assert normalized.get("lr_scheme_code") is None
    assert worker._task_norm({"pool_size": 999}, lot)["pool_size"] == RO_MAX[game]


@pytest.mark.parametrize("game", RO_MAX)
def test_pool_below_the_ui_minimum_climbs_to_the_smallest_lr_scheme(
    monkeypatch, game
):
    """Sub minimul UI urcă la cel mai mic cod LR; motorul acceptă în continuare
    pool-urile mici de referință cerute programatic (bench, studii WF)."""
    settings = _settings(monkeypatch)
    key = ui_runtime._POOL_KEYS[game]
    for below in (0, 1, RO_MIN[game] - 1):
        settings[key] = below
        assert ui_runtime._pool_size_for(game) == RO_MIN[game]
    lot = L.GAMES_BY_ID[game]
    assert worker._task_norm({"pool_size": 6}, lot)["pool_size"] == 6


def test_settings_reset_at_restart_and_remain_editable_in_session(tmp_path, monkeypatch):
    settings = _settings(monkeypatch)
    saved = {ui_runtime._POOL_KEYS[g]: maximum for g, maximum in RO_MAX.items()}
    saved.update(pool_size_val=14, guarantee_val=3, max_variants_val=123,
                 wf_depth_pct=70, mail_on_complete=True)
    state = tmp_path / "ui-state.json"
    state.write_text(json.dumps(saved), encoding="utf-8")
    monkeypatch.setattr(ui_runtime, "UI_STATE_FILE", state)

    ui_runtime._load_settings()  # process startup
    assert {g: ui_runtime._pool_size_for(g) for g in RO_MAX} == RO_DEFAULT
    for key in ("pool_size_val", "guarantee_val", "max_variants_val",
                "wf_depth_pct", "mail_on_complete"):
        assert settings[key] == saved[key]

    choices = {"6/49": 12, "5/40": 17, "joker": 15}
    explicit_codes = {"6/49": 58, "5/40": 46, "joker": 12}
    for game, value in choices.items():
        settings[ui_runtime._POOL_KEYS[game]] = value
        settings[ui_runtime._LR_CODE_KEYS[game]] = explicit_codes[game]
    ui_runtime._save_settings()
    # A second client sees the shared session settings without a reload.
    assert {g: ui_runtime._pool_size_for(g) for g in RO_MAX} == choices
    assert all(json.loads(state.read_text())[ui_runtime._POOL_KEYS[g]] == v
               for g, v in choices.items())
    assert {g: ui_runtime._lr_scheme_for(g).code for g in choices} == explicit_codes
    assert all(json.loads(state.read_text())[ui_runtime._LR_CODE_KEYS[g]] == code
               for g, code in explicit_codes.items())
    ui_runtime._load_settings()  # next process startup
    assert {g: ui_runtime._pool_size_for(g) for g in RO_MAX} == RO_DEFAULT
    assert settings["guarantee_val"] == 3
    assert settings["max_variants_val"] == 123
    assert {g: ui_runtime._lr_scheme_for(g).code for g in choices} == ui_runtime._LR_CODE_DEFAULTS


def test_real_process_restart_restores_only_romanian_pool_defaults(tmp_path):
    import subprocess
    import sys

    state = tmp_path / "ui-state.json"
    state.write_text(json.dumps({
        "pool_size_649_val": 30, "pool_size_540_val": 25,
        "pool_size_joker_val": 30, "pool_size_val": 14,
        "guarantee_val": 3, "max_variants_val": 123,
    }), encoding="utf-8")
    script = r"""
import json, sys
from pathlib import Path
import ui_runtime as u
u.UI_STATE_FILE = Path(sys.argv[1])
u._load_settings()
keys = list(u._POOL_KEYS.values()) + list(u._LR_CODE_KEYS.values()) + ['pool_size_val', 'guarantee_val', 'max_variants_val']
print(json.dumps({k: u.SETTINGS[k] for k in keys}))
if sys.argv[2] == 'edit':
    u.SETTINGS.update(pool_size_649_val=12, pool_size_540_val=17, pool_size_joker_val=15,
                      lr_scheme_649_val=58, lr_scheme_540_val=46, lr_scheme_joker_val=12)
    u._save_settings()
"""
    def boot(action):
        result = subprocess.run(
            [sys.executable, "-c", script, str(state), action],
            check=True, text=True, capture_output=True,
        )
        return json.loads(result.stdout.splitlines()[-1])

    expected = {
        "pool_size_649_val": 9, "pool_size_540_val": 8,
        "pool_size_joker_val": 10, "pool_size_val": 14,
        "guarantee_val": 3, "max_variants_val": 123,
        "lr_scheme_649_val": 48, "lr_scheme_540_val": 16, "lr_scheme_joker_val": 24,
    }
    assert boot("edit") == expected
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["pool_size_joker_val"] == 15
    assert {g: saved[ui_runtime._LR_CODE_KEYS[g]] for g in RO_MAX} == {
        "6/49": 58, "5/40": 46, "joker": 12,
    }
    assert boot("read") == expected


def test_previous_joker_default_resets_to_played_lr_pool_without_rewriting_saved_settings(
    tmp_path, monkeypatch
):
    settings = _settings(monkeypatch)
    saved = {
        "pool_size_649_val": 16,
        "pool_size_540_val": 19,
        "pool_size_joker_val": 12,
        "pool_size_val": 13,
        "guarantee_val": 4,
        "max_variants_val": 118,
        "wf_depth_pct": 70,
        "bench_hit_target": ui_runtime.DEFAULT_BENCH_HIT_TARGET,
        "bench_target_revision": ui_runtime.BENCH_TARGET_REVISION,
        "mail_on_complete": False,
    }
    state = tmp_path / "old-defaults.json"
    state.write_text(json.dumps(saved), encoding="utf-8")
    before = state.read_bytes()
    monkeypatch.setattr(ui_runtime, "UI_STATE_FILE", state)

    ui_runtime._load_settings()

    assert ui_runtime._pool_size_for("joker") == 10
    assert L.GAMES_BY_ID["joker"].default_pool == 15  # registry/catalog threshold unchanged
    assert {g: ui_runtime._pool_size_for(g) for g in RO_MAX} == RO_DEFAULT
    for key, value in saved.items():
        if key not in ui_runtime._POOL_KEYS.values():
            assert settings[key] == value
    assert state.read_bytes() == before


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


def test_config_contains_each_largest_lr_pool_and_the_exact_official_budget(monkeypatch):
    values = {ui_runtime._POOL_KEYS[g]: maximum for g, maximum in RO_LR_MAX.items()}
    maximum_schemes = {g: max(lr_schemes(g), key=lambda s: (s.n, s.size, s.code)) for g in RO_MAX}
    values.update({ui_runtime._LR_CODE_KEYS[g]: scheme.code for g, scheme in maximum_schemes.items()})
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
    assert {task["game_label"]: task["pool_size"] for task in tasks} == RO_LR_MAX
    assert {task["game_label"]: task["lr_scheme_code"] for task in tasks} == {
        g: scheme.code for g, scheme in maximum_schemes.items()
    }
    assert {task["game_label"]: task["max_variants"] for task in tasks} == {
        g: scheme.size for g, scheme in maximum_schemes.items()
    }
    assert settings["max_variants_val"] == 120


def test_lr_selection_explanations_follow_exact_codes_without_mutating_settings(monkeypatch):
    settings = _settings(monkeypatch)
    assert ui_runtime._large_pool_notes() == []
    for g, maximum in RO_LR_MAX.items():
        scheme = next(s for s in lr_schemes(g) if s.n == maximum)
        settings[ui_runtime._POOL_KEYS[g]] = maximum
        settings[ui_runtime._LR_CODE_KEYS[g]] = scheme.code
    settings["max_variants_val"] = 100
    before = dict(settings)
    notes = ui_runtime._large_pool_notes()
    assert notes == []
    for game in RO_MAX:
        scheme = ui_runtime._lr_scheme_for(game)
        summary, guarantee = ui_runtime._lr_selection_lines(game)
        assert f"Cod {scheme.code}" in summary
        assert f"{scheme.n} numere" in summary
        assert f"{scheme.size} variante" in summary
        assert f"{scheme.size * L.GAMES_BY_ID[game].price:g} lei" in summary
        assert guarantee == "Garanții condiționate: " + app._lr_guarantee_text(scheme)
    assert settings == before
    settings["max_variants_val"] = 0
    settings["guarantee_val"] = 6
    assert ui_runtime._large_pool_notes() == []
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
        "bench_was_running": False, "_bench_history_pending": None,
    }
    monkeypatch.setattr(app, "STATE", state)
    # This tests WF resource refusal, independently of any prior/live Re-Bench.
    monkeypatch.setattr(app, "_bench_running", lambda: False)
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
def test_small_pool_result_cache_keys_keep_task_signature_and_scope_by_catalog(
    tmp_path, monkeypatch, game, pool, expected
):
    from loto_enterprise.benchmark import methods
    from loto_enterprise.core import walk_forward_adapter as wf

    monkeypatch.setattr(worker, "PROJECT_ROOT", tmp_path)
    monkeypatch.delenv("LOTO_WHEEL_METHOD", raising=False)
    monkeypatch.setattr(wf, "_wheel_sig", lambda *args: "fixed-design-signature")
    config = {"datasets": [{"tasks": [{
        "game_label": game, "pool_size": pool, "guarantee": 4, "max_variants": 50,
    }]}]}
    key = worker._pipeline_cache_key("fixed-history", config)
    assert key == f"v23:fixed-history:wheel=auto:catalog=compact-v3:{expected}"
    monkeypatch.setattr(methods, "CATALOG_VERSION", "compact-v2")
    assert worker._pipeline_cache_key("fixed-history", config) == key.replace("compact-v3", "compact-v2")


def test_large_pool_result_cache_has_its_own_namespace(tmp_path, monkeypatch):
    from loto_enterprise.core import walk_forward_adapter as wf

    monkeypatch.setattr(worker, "PROJECT_ROOT", tmp_path)
    monkeypatch.delenv("LOTO_WHEEL_METHOD", raising=False)
    monkeypatch.setattr(wf, "_wheel_sig", lambda *args: "fixed-design-signature")
    config = {"datasets": [{"tasks": [{
        "game_label": "6/49", "pool_size": 49, "guarantee": 4, "max_variants": 50,
    }]}]}
    key = worker._pipeline_cache_key("fixed-history", config)
    namespace = "v23:fixed-history:wheel=auto:catalog=compact-v3"
    expected = "e80ea7ae3d3a93d1b94aecc63156d7cdc03cffecf27c1901b98e98884e81c6de"
    assert key == f"{namespace}:{expected}:lp2"


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
    assert not worker._pipeline_cache_key("same-history", config).endswith(":lp2")
    config["datasets"][0]["tasks"][0]["pool_size"] = new_size
    assert worker._pipeline_cache_key("same-history", config).endswith(":lp2")
