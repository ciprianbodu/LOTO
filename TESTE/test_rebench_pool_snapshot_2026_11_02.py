"""Poolurile și sursele validate corespund momentului lansării Re-Bench."""
import json
from types import SimpleNamespace

import app_nicegui as app
import bench_outcome
import pytest
from loto_enterprise.core import walk_forward_adapter as wf
from TESTE.test_rebench_selected_sources import session
from TESTE.test_virtual_history_ui_2026_11_01 import virtual_setup
from TESTE.test_wf_incremental import wf as wf_backend, history


def _pools():
    return {g.game_id: app._pool_size_for(g.game_id) for g in app._LOT.GAMES}


def test_new_games_use_their_launch_pool_and_original_game_keeps_source(session, monkeypatch):
    _games, original = session
    monkeypatch.setitem(app.SETTINGS, "games_val", ["6/49"])
    context = (app._build_config_json(), _pools())
    monkeypatch.setitem(app.SETTINGS, "games_val", ["6/49", "joker", "5/40"])
    app._load_missing_bench_histories()
    monkeypatch.setitem(app.SETTINGS, "pool_size_649_val", 30)
    monkeypatch.setitem(app.SETTINGS, "pool_size_joker_val", 20)
    monkeypatch.setitem(app.SETTINGS, "pool_size_540_val", 25)
    changed = original.copy()
    changed.loc[0, "n1"] = 30
    monkeypatch.setitem(app.STATE, "datasets", [("custom_649.csv", changed)] + app.STATE["datasets"][1:])
    result = json.loads(app._bench_history_config(context))
    games = {d["tasks"][0]["game_label"]: d for d in result["datasets"]}
    assert {k: d["tasks"][0]["pool_size"] for k, d in games.items()} == {"6/49": 16, "joker": 12, "5/40": 19}
    assert json.loads(games["6/49"]["df_json"])["data"][0][1] == 9


def test_rebench_starts_compact_actual_variants_with_frozen_pool(session, monkeypatch):
    _games, _original = session
    monkeypatch.setitem(app.SETTINGS, "games_val", ["6/49"])
    context = (app._build_config_json(), _pools())
    monkeypatch.setitem(app.SETTINGS, "pool_size_649_val", 30)
    calls = []
    monkeypatch.setattr(app, "_start_virtual_history", lambda **kw: calls.append(kw) or True)
    app._on_bench_finished(context=context)
    assert calls[0]["simulated"] and calls[0]["materialize_full"]
    task = json.loads(calls[0]["config_json"])["datasets"][0]["tasks"][0]
    assert task["pool_size"] == 16 and task["guarantee"] == 6
    assert app.STATE["result_sources"]["custom_649.csv"] is session[1]


def test_resumed_running_bench_uses_persisted_context_not_current_settings(session, monkeypatch, tmp_path):
    monkeypatch.setattr(bench_outcome, "RUNTIME_ROOT", tmp_path)
    run_id, pid = "c" * 32, 1234
    context = (app._build_config_json(), _pools())
    digest = bench_outcome.save_context(run_id, *context)
    bench_outcome.record_exit(run_id, pid, 0)
    monkeypatch.setitem(app.STATE, "_bench_run_identity", (pid, run_id, digest))
    monkeypatch.setitem(app.STATE, "_bench_process", None)
    monkeypatch.setitem(app.STATE, "bench_was_running", True)
    monkeypatch.setitem(app.STATE, "bench_cancelled", False)
    monkeypatch.setitem(app.SETTINGS, "pool_size_649_val", 30)
    calls = []
    monkeypatch.setattr(app, "_on_bench_finished", lambda **kw: calls.append(kw))
    app._observe_bench_activity(False)
    assert calls == [{"context": context}]
    assert bench_outcome.is_claimed(run_id)
    assert bench_outcome.load_context(run_id, digest) is None


def test_delayed_history_keeps_pool_after_settings_change(session, monkeypatch):
    context = (app._build_config_json(), _pools())
    monkeypatch.setitem(app.STATE, "active_job_id", 12)
    monkeypatch.setattr(app, "_virtual_decision_fingerprint", lambda *_: (3, "same"))
    app._on_bench_finished(context=context)
    monkeypatch.setitem(app.SETTINGS, "pool_size_649_val", 30)
    monkeypatch.setitem(app.STATE, "active_job_id", None)
    monkeypatch.setitem(app.STATE, "_autopilot_after_virtual", None)
    app._VIRTUAL_WF_CANCEL_REQUESTED.clear()
    calls = []
    monkeypatch.setattr(app, "_start_virtual_history", lambda **kw: calls.append(kw) or True)
    app._continue_after_automatic_history(False)
    games = {d["tasks"][0]["game_label"]: d for d in json.loads(calls[0]["config_json"])["datasets"]}
    assert games["6/49"]["tasks"][0]["pool_size"] == 16


def test_previous_history_is_marked_when_a_new_bench_starts(session, monkeypatch, tmp_path):
    monkeypatch.setattr(bench_outcome, "RUNTIME_ROOT", tmp_path)
    monkeypatch.setattr(app, "BENCH_PID_FILE", tmp_path / "pid")
    monkeypatch.setattr(app.subprocess, "Popen", lambda *_a, **_kw: SimpleNamespace(pid=123))
    monkeypatch.setattr(app, "_refresh_status", lambda: None)
    for key in ("_bench_process", "_bench_run_identity", "_bench_context_required", "bench_was_running", "virtual_wf_previous", "virtual_wf_dirty"):
        monkeypatch.setitem(app.STATE, key, app.STATE.get(key))
    old = [{"name": "previous", "pool": 30}]
    monkeypatch.setitem(app.STATE, "virtual_wf_results", old)
    app._launch_bench([], "Re-Bench")
    assert app.STATE["virtual_wf_results"] is old
    assert app.STATE["virtual_wf_previous"] is True


def test_corrupted_saved_context_is_not_accepted(tmp_path, monkeypatch):
    monkeypatch.setattr(bench_outcome, "RUNTIME_ROOT", tmp_path)
    run_id = "d" * 32
    digest = bench_outcome.save_context(run_id, '{"datasets": []}', {"6/49": 16})
    path = bench_outcome._path(run_id).with_suffix(".context")
    path.write_text(path.read_text().replace('16', '30'))
    assert bench_outcome.load_context(run_id, digest) is None


@pytest.mark.parametrize("resumed", [False, True])
def test_missing_new_context_does_not_run_with_current_settings(session, tmp_path, monkeypatch, resumed):
    monkeypatch.setattr(bench_outcome, "RUNTIME_ROOT", tmp_path)
    run_id, pid = "e" * 32, 1234
    digest = bench_outcome.save_context(run_id, app._build_config_json(), _pools())
    bench_outcome.record_exit(run_id, pid, 0)
    bench_outcome.clear_context(run_id)
    marker = tmp_path / "pid"
    marker.write_text(f"{pid}|1|{run_id}|{digest}|ctx1")
    monkeypatch.setattr(app, "BENCH_PID_FILE", marker)
    monkeypatch.setitem(app.STATE, "_bench_run_identity", (pid, run_id, digest))
    monkeypatch.setitem(app.STATE, "_bench_process", None if resumed else SimpleNamespace(poll=lambda: 0))
    monkeypatch.setitem(app.STATE, "_bench_context_required", None if resumed else run_id)
    monkeypatch.setitem(app.STATE, "bench_was_running", True)
    monkeypatch.setitem(app.STATE, "bench_cancelled", False)
    calls = []
    monkeypatch.setattr(app, "_on_bench_finished", lambda **kw: calls.append(kw))
    app._observe_bench_activity(False)
    assert calls == []
    assert "nu mai sunt disponibile" in app.STATE["virtual_wf_status"]


def test_reattached_ui_remembers_context_requirement_if_marker_changes(session, tmp_path, monkeypatch):
    import psutil
    monkeypatch.setattr(bench_outcome, "RUNTIME_ROOT", tmp_path)
    run_id, pid = "f" * 32, 1234
    digest = bench_outcome.save_context(run_id, app._build_config_json(), _pools())
    marker = tmp_path / "pid"
    marker.write_text(f"{pid}|1|{run_id}|{digest}|ctx1")
    monkeypatch.setattr(app, "BENCH_PID_FILE", marker)
    monkeypatch.setattr(psutil, "Process", lambda *_: SimpleNamespace(status=lambda: "running", create_time=lambda: 1))
    monkeypatch.setattr(app, "_bench_process_identity", lambda _: True)
    for key in ("_bench_run_identity", "_bench_context_required", "_bench_seen_run", "bench_was_running"):
        monkeypatch.setitem(app.STATE, key, None)
    assert app._bench_pid_status() == (pid, True)
    assert app.STATE["_bench_context_required"] == run_id
    marker.unlink()
    bench_outcome.clear_context(run_id)
    bench_outcome.record_exit(run_id, pid, 0)
    monkeypatch.setitem(app.STATE, "_bench_process", None)
    monkeypatch.setitem(app.STATE, "bench_cancelled", False)
    calls = []
    monkeypatch.setattr(app, "_on_bench_finished", lambda **kw: calls.append(kw))
    app._observe_bench_activity(False)
    assert calls == []
    assert "nu mai sunt disponibile" in app.STATE["virtual_wf_status"]


def test_confirmed_cancellation_removes_only_its_context(session, tmp_path, monkeypatch):
    import psutil
    monkeypatch.setattr(bench_outcome, "RUNTIME_ROOT", tmp_path)
    own, other, pid = "1" * 32, "2" * 32, 1234
    digest = bench_outcome.save_context(own, app._build_config_json(), _pools())
    other_digest = bench_outcome.save_context(other, app._build_config_json(), _pools())
    marker = tmp_path / "pid"
    marker.write_text(f"{pid}|1|{own}|{digest}|ctx1")
    monkeypatch.setattr(app, "BENCH_PID_FILE", marker)
    monkeypatch.setattr(psutil, "Process", lambda *_: (_ for _ in ()).throw(psutil.NoSuchProcess(pid)))
    monkeypatch.setattr(psutil, "process_iter", lambda *_: [])
    monkeypatch.setattr(app, "cancel_pending_running_jobs", lambda *_: None)
    monkeypatch.setattr(app, "_refresh_status", lambda: None)
    monkeypatch.setitem(app.STATE, "_bench_run_identity", (pid, own, digest))
    monkeypatch.setitem(app.STATE, "_bench_context_required", own)
    monkeypatch.setitem(app.STATE, "_bench_survivors", [])
    app.cancel_all()
    assert bench_outcome.load_context(own, digest) is None
    assert bench_outcome.load_context(other, other_digest) is not None


@pytest.mark.parametrize(("game", "pool", "guarantee", "count", "maximum"), [
    ("6/49", 16, 6, 8008, 49), ("5/40", 19, 5, 11628, 40),
])
def test_real_full_wf_generates_compact_variants_and_prize_groups(wf_backend, game, pool, guarantee, count, maximum):
    import numpy as np
    import pandas as pd
    source = history(50)
    if maximum == 40:
        rng = np.random.default_rng(42)
        source[[f"n{i}" for i in range(1, 7)]] = [sorted(rng.choice(np.arange(1, 41), 6, replace=False)) for _ in range(50)]
    rows, meta = wf_backend.run_honest_walk_forward(source, game_type=game, pool_size=pool,
        backtest_depth_percent=2, guarantee=guarantee, max_variants=0, compact_results=True)
    assert meta["n_test_draws"] == 1 and len(rows) == 1
    assert rows[0].variant_count == count
    assert rows[0].variant_prize_groups
    assert not rows[0].virtual_full_system


def test_full_physical_and_too_large_virtual_are_labeled_per_game(virtual_setup, monkeypatch):
    virtual_setup[:] = virtual_setup[:2]
    virtual_setup[0]["tasks"][0].update(pool_size=30, guarantee=6)
    virtual_setup[1]["tasks"][0].update(pool_size=12, guarantee=5)
    calls = []
    def run(**kwargs):
        calls.append(kwargs)
        virtual = bool(kwargs.get("virtual_full_system"))
        row = SimpleNamespace(draw_index=1, draw_date="2026-10-29", variant=[1, 2, 3, 4, 5],
                              hits=2, hits_union=2, variant_count=792, virtual_full_system=virtual,
                              ticket_context={"actual": [1,2,3,4,5], "hard_core": list(range(1, 13))})
        return [row], {"virtual_full_system": virtual, "n_test_draws": 1, "n_expected": 1}
    monkeypatch.setattr(wf, "run_honest_walk_forward", run)
    assert app._start_virtual_history(automatic=True, simulated=True, materialize_full=True)
    assert calls[0].get("virtual_full_system") is True
    assert calls[1].get("compact_results") is True
    assert [r["simulated"] for r in app.STATE["virtual_wf_results"]] == [False, True]


def test_mixed_reduced_full_and_too_large_select_per_game(virtual_setup, monkeypatch):
    virtual_setup[0]["tasks"][0].update(pool_size=10, guarantee=4, max_variants=100)
    virtual_setup[1]["tasks"][0].update(pool_size=12, guarantee=5)
    virtual_setup[2]["tasks"][0].update(pool_size=30, guarantee=5)
    calls = []
    def run(**kwargs):
        calls.append(kwargs)
        virtual = bool(kwargs.get("virtual_full_system"))
        row = SimpleNamespace(draw_index=1, draw_date="2026-10-29", variant=[1,2,3,4,5],
                              hits=2, hits_union=2, variant_count=10, virtual_full_system=virtual,
                              ticket_context={"actual": [1,2,3,4,5], "hard_core": list(range(1, 13))})
        return [row], {"virtual_full_system": virtual, "n_test_draws": 1, "n_expected": 1}
    monkeypatch.setattr(wf, "run_honest_walk_forward", run)
    assert app._start_virtual_history(automatic=True, simulated=True, materialize_full=True)
    assert [r["simulated"] for r in app.STATE["virtual_wf_results"]] == [True, True, False]
    assert calls[0].get("compact_results") and calls[1].get("compact_results")
    assert calls[2].get("virtual_full_system")
