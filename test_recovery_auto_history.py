"""Recalcularea sigură a istoricului unui rezultat recuperat."""
import pytest
import app_nicegui as app
from loto_enterprise.core import walk_forward_adapter as wf
from test_recovered_manual_wf_2026_11_01 import recovered


@pytest.fixture
def background(recovered, monkeypatch):
    monkeypatch.setitem(app.STATE, "recovered_wf_auto_attempted", None)
    monkeypatch.setitem(app.STATE, "virtual_wf_running", False)
    monkeypatch.setitem(app.STATE, "bench_was_running", False)
    monkeypatch.setattr(app, "_bench_running", lambda: False)
    monkeypatch.setattr(app.ui, "notify", lambda *a, **k: pytest.fail("notification requires a client"))
    monkeypatch.setattr(app.results_panel, "refresh", lambda: pytest.fail("refresh requires a client"))
    monkeypatch.setattr(app, "_finalize_pipeline", lambda: pytest.fail("shutdown"))
    monkeypatch.setattr(app, "_maybe_send_results_email", lambda: pytest.fail("mail"))
    return recovered


def test_missing_history_uses_original_snapshot_once_without_ui_or_side_effects(background, monkeypatch):
    frame, data, notices = background
    calls = []
    monkeypatch.setattr(wf, "run_honest_walk_forward", lambda **kw: (
        calls.append(kw) or [{"draw_index": 1}],
        {"partial": False, "n_test_draws": 1, "n_expected": 1},
    ))
    assert app._start_recovered_history_if_missing()
    assert calls[0]["df_source"] is frame
    assert calls[0]["pool_size"] == data["pool_size"]
    assert calls[0]["guarantee"] == data["guarantee"]
    assert app.STATE["retro"]["history.csv_6/49"] == [{"draw_index": 1}]
    assert not app._start_recovered_history_if_missing()
    assert len(calls) == 1


@pytest.mark.parametrize("metadata,rows,expected", [
    ({"partial": False}, ["done"], False),
    ({"partial": True}, ["partial"], True),
    ({"large_pool_wf_skipped": True}, [], False),
    ({"decision_moved": True}, [], False),
    ({"scorer_fallback": True}, [], False),
])
def test_cache_complete_partial_and_explicit_skips(background, monkeypatch, metadata, rows, expected):
    key = "history.csv_6/49"
    monkeypatch.setitem(app.STATE, "retro", {key: rows})
    monkeypatch.setitem(app.STATE, "retro_meta", {key: metadata})
    starts = []
    monkeypatch.setattr(app, "_start_walk_forward", lambda **kw: starts.append(kw))
    assert app._start_recovered_history_if_missing() is expected
    assert bool(starts) is expected
    if starts:
        assert starts == [{"post_completion_actions": False}]
        assert not app._start_recovered_history_if_missing()


@pytest.mark.parametrize("source", [None, {}, {"other.csv": object()}])
def test_missing_original_snapshot_never_uses_current_datasets(background, monkeypatch, source):
    monkeypatch.setitem(app.STATE, "result_sources", source)
    monkeypatch.setitem(app.STATE, "datasets", [("history.csv", object())])
    monkeypatch.setattr(app, "_start_walk_forward", lambda **kw: pytest.fail("missing source"))
    assert not app._start_recovered_history_if_missing()


@pytest.mark.parametrize("busy", ["wf_running", "virtual_wf_running", "active_job_id", "bench_was_running"])
def test_no_overlap_with_existing_work(background, monkeypatch, busy):
    monkeypatch.setitem(app.STATE, busy, True)
    monkeypatch.setattr(app, "_start_walk_forward", lambda **kw: pytest.fail("overlap"))
    assert not app._start_recovered_history_if_missing()
    assert app.STATE["recovered_wf_auto_attempted"] is None


def test_recovery_starts_after_cache_load_without_client(background, monkeypatch):
    frame, data, _ = background
    payload = app.STATE["results"]
    monkeypatch.setattr(app, "get_latest_completed_job", lambda: {
        "id": 28, "result_json": "{}", "ui_finalized_at": "2026-11-01T12:00:00"})
    monkeypatch.setattr(app, "decode_queue_result", lambda _: payload)
    monkeypatch.setattr(app, "_result_sources_from_job", lambda _: {"history.csv": frame})
    monkeypatch.setattr(app, "_completed_age_seconds", lambda _: 10000)
    monkeypatch.setattr(app, "_completed_local_text", lambda _: "earlier")
    events = []
    monkeypatch.setattr(app, "_load_cached_walk_forward", lambda: events.append("load"))
    monkeypatch.setattr(app, "_start_walk_forward", lambda **kw: events.append(("run", kw)))
    app._recover_completed_job(allow_finalize=False)
    assert events == ["load", ("run", {"post_completion_actions": False})]
    assert app.STATE["result_sources"]["history.csv"] is frame
    app._recover_completed_job(allow_finalize=False)
    assert len([e for e in events if isinstance(e, tuple)]) == 1


def test_deferred_recovery_is_not_restarted_after_cancel_all(background, monkeypatch, tmp_path):
    import psutil
    monkeypatch.setitem(app.STATE, "bench_was_running", True)
    assert not app._start_recovered_history_if_missing()
    monkeypatch.setattr(app, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(app, "BENCH_PID_FILE", tmp_path / "pid")
    monkeypatch.setattr(app, "_bench_pid_status", lambda: (None, False))
    monkeypatch.setattr(psutil, "process_iter", lambda *_: [])
    monkeypatch.setattr(app, "cancel_pending_running_jobs", lambda *_: None)
    monkeypatch.setattr(app, "_refresh_status", lambda: None)
    monkeypatch.setattr(app.ui, "notify", lambda *_a, **_kw: None)
    monkeypatch.setattr(app, "_start_walk_forward", lambda **kw: pytest.fail("cancelled recovery restarted"))
    app.cancel_all()
    monkeypatch.setitem(app.STATE, "bench_was_running", False)
    assert not app._start_recovered_history_if_missing()
