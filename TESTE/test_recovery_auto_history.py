"""Pornirea reafișează cache-ul, fără să pornească o nouă validare."""
import hashlib

import pytest
import psutil
import app_nicegui as app
import bench_outcome
from loto_enterprise.core import walk_forward_adapter as wf
from TESTE.test_recovered_manual_wf_2026_11_01 import recovered


@pytest.mark.parametrize("taken,allow_finalize", [(False, True), (False, False), (True, True)])
@pytest.mark.parametrize("cached", ["missing", "partial", "complete"])
def test_recovery_loads_only_cached_data_even_for_new_job(recovered, monkeypatch, taken, allow_finalize, cached):
    frame, _data, _ = recovered
    payload = app.STATE["results"]
    monkeypatch.setattr(app, "get_latest_completed_job", lambda: {
        "id": 28, "result_json": "{}", "ui_finalized_at": "done" if taken else None})
    monkeypatch.setattr(app, "decode_queue_result", lambda _: payload)
    monkeypatch.setattr(app, "_result_sources_from_job", lambda _: {"history.csv": frame})
    monkeypatch.setattr(app, "_completed_local_text", lambda _: "acum")
    monkeypatch.setattr(app, "_result_scorers_match_decision", lambda *_: True)
    monkeypatch.setattr(app, "_wf_run_kwargs", lambda *_: {})
    calls, marks = [], []
    rows = [] if cached == "missing" else [{"draw_index": 1}]
    meta = {"partial": cached == "partial"}
    monkeypatch.setattr(wf, "run_honest_walk_forward", lambda **kw: (calls.append(kw) or rows, meta))
    monkeypatch.setattr(app, "_mark_job_finalized", marks.append)
    monkeypatch.setattr(app, "_start_walk_forward", lambda **kw: pytest.fail("new walk-forward on startup"))
    monkeypatch.setattr(app, "_maybe_send_results_email", lambda: pytest.fail("mail on startup"))
    monkeypatch.setattr(app, "_finalize_pipeline", lambda: pytest.fail("shutdown on startup"))
    app._recover_completed_job(allow_finalize=allow_finalize)
    assert app.STATE["active_job_id"] is None
    assert app.STATE["results"] == payload
    assert app.STATE["result_sources"]["history.csv"] is frame
    assert app.STATE["results_recovered"].startswith("job #28")
    assert len(calls) == 1 and calls[0]["cache_only"] is True and calls[0]["use_cache"] is True
    assert calls[0]["df_source"] is frame
    assert marks == ([] if taken else [28])
    if rows:
        assert app.STATE["retro"]["history.csv_6/49"] == rows
        assert app.STATE["retro_meta"]["history.csv_6/49"]["partial"] == (cached == "partial")


def _marker_fixture(monkeypatch, tmp_path, alive):
    run_id, pid, timestamp = "a" * 32, 12345, 1000
    digest = hashlib.sha256(b"same").hexdigest()
    marker = tmp_path / "bench_pid"
    marker.write_text(f"{pid}|{timestamp}|{run_id}|{digest}", encoding="utf-8")
    monkeypatch.setattr(app, "BENCH_PID_FILE", marker)
    monkeypatch.setattr(bench_outcome, "RUNTIME_ROOT", tmp_path)
    monkeypatch.setattr(app, "_build_config_json", lambda: "same")
    monkeypatch.setattr(app, "_bench_process_identity", lambda _: True)
    monkeypatch.setattr(app, "_bench_survivor_identity", lambda *_: False)
    for key, value in {"_bench_run_identity": None, "_bench_seen_run": None,
                       "_bench_survivors": [], "bench_was_running": False,
                       "bench_cancelled": False}.items():
        monkeypatch.setitem(app.STATE, key, value)
    class Process:
        def __init__(self, given):
            if not alive[0]:
                raise psutil.NoSuchProcess(given)
        def status(self): return "running"
        def create_time(self): return timestamp
    monkeypatch.setattr(psutil, "Process", Process)
    return run_id, pid


def test_bench_completed_before_start_is_suppressed_locally_and_never_triggers(recovered, monkeypatch, tmp_path):
    run_id, pid = _marker_fixture(monkeypatch, tmp_path, [False])
    bench_outcome.record_exit(run_id, pid, 0)
    monkeypatch.setattr(app, "_on_bench_finished", lambda: pytest.fail("stale bench triggered"))
    app._discard_finished_bench_at_startup()
    assert not bench_outcome.is_claimed(run_id)
    assert app.STATE["bench_was_running"] is False
    app._observe_bench_activity(app._bench_running())
    # A further UI restart suppresses the old outcome locally again.
    app.STATE["_bench_seen_run"] = None
    app._discard_finished_bench_at_startup()
    app._observe_bench_activity(app._bench_running())


def test_bench_running_at_start_triggers_once_when_it_later_finishes(recovered, monkeypatch, tmp_path):
    alive = [True]
    run_id, pid = _marker_fixture(monkeypatch, tmp_path, alive)
    events = []
    monkeypatch.setattr(app, "_on_bench_finished", lambda: events.append("bench"))
    app._discard_finished_bench_at_startup()
    assert app.STATE["bench_was_running"] is True
    assert not bench_outcome.is_claimed(run_id)
    bench_outcome.record_exit(run_id, pid, 0)
    alive[0] = False
    app._observe_bench_activity(app._bench_running())
    app._observe_bench_activity(app._bench_running())
    assert events == ["bench"]
    assert bench_outcome.is_claimed(run_id)


def test_running_job_reattached_at_start_finishes_with_normal_actions(recovered, monkeypatch):
    from scripts.analysis.audit_output import capture_ui
    frame, _data, _ = recovered
    payload = app.STATE["results"]
    events = []
    for name in ("init_job_queue", "_load_settings", "_migrate_legacy_finalized_marker",
                 "_discard_finished_bench_at_startup"):
        monkeypatch.setattr(app, name, lambda: None)
    monkeypatch.setattr(app, "_decision_target_mismatch", lambda: False)
    monkeypatch.setattr(app, "is_fresh_ui_start", lambda: False)
    monkeypatch.setattr(app, "get_active_job", lambda: {"id": 28, "status": "RUNNING"})
    monkeypatch.setattr(app, "is_unstarted_job", lambda _: False)
    monkeypatch.setattr(app, "ensure_worker_running", lambda: events.append("worker"))
    monkeypatch.setattr(app, "_recover_completed_job", lambda **_: pytest.fail("recovered while active"))
    monkeypatch.setattr(app, "_bench_running", lambda: False)
    monkeypatch.setitem(app.STATE, "active_job_id", None)
    app._startup()
    assert app.STATE["active_job_id"] == 28 and events == ["worker"]
    monkeypatch.setattr(app, "get_job_status", lambda _: {"id": 28, "status": "COMPLETED", "result_json": "{}"})
    monkeypatch.setattr(app, "decode_queue_result", lambda _: payload)
    monkeypatch.setattr(app, "_result_sources_from_job", lambda _: {"history.csv": frame})
    monkeypatch.setattr(app, "_mark_job_finalized", lambda _: events.append("claimed"))
    monkeypatch.setattr(app, "_maybe_send_results_email", lambda: events.append("mail"))
    monkeypatch.setattr(app, "_start_walk_forward", lambda **_: events.append("wf"))
    monkeypatch.setattr(app, "_shutdown_banner", lambda: None)
    with capture_ui():
        app.status_panel.func()
    assert events == ["worker", "claimed", "mail", "wf"]
    assert app.STATE["results_recovered"] is None


def test_second_ui_startup_does_not_consume_first_uis_bench_outcome(recovered, monkeypatch, tmp_path):
    alive = [False]
    run_id, pid = _marker_fixture(monkeypatch, tmp_path, alive)
    bench_outcome.record_exit(run_id, pid, 0)
    digest = hashlib.sha256(b"same").hexdigest()
    class FinishedProcess:
        def poll(self): return 0
    owner = dict(app.STATE)
    owner.update({"bench_was_running": True, "_bench_run_identity": (pid, run_id, digest),
                  "_bench_process": FinishedProcess(), "_bench_seen_run": None})
    newcomer = dict(app.STATE)
    newcomer.update({"bench_was_running": False, "_bench_run_identity": None,
                     "_bench_process": None, "_bench_seen_run": None})
    events = []
    monkeypatch.setattr(app, "_on_bench_finished", lambda: events.append("history"))

    # A second UI starts after the benchmark has exited, but before its owner
    # has observed completion. Its local suppression must not claim the receipt.
    monkeypatch.setattr(app, "STATE", newcomer)
    app._discard_finished_bench_at_startup()
    app._observe_bench_activity(app._bench_running())
    assert not events and not bench_outcome.is_claimed(run_id)

    # The original UI can still claim and process its own successful run once.
    monkeypatch.setattr(app, "STATE", owner)
    app._observe_bench_activity(app._bench_running())
    app._observe_bench_activity(app._bench_running())
    assert events == ["history"] and bench_outcome.is_claimed(run_id)
