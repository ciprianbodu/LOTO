import asyncio
from functools import partial
import sqlite3
import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import app_nicegui as app
import job_queue as queue
from ui_job_status import JobStatusProbe


async def _until(predicate):
    for _ in range(200):
        if predicate():
            return
        await asyncio.sleep(.01)
    pytest.fail("background operation did not finish")


def test_exclusive_sqlite_lock_retains_snapshot_and_does_not_block_or_duplicate_polling(tmp_path):
    path = str(tmp_path / "queue.sqlite")
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE status (step INTEGER)")
        connection.execute("INSERT INTO status VALUES (25)")
    probe, calls = JobStatusProbe(), []
    def read(job):
        calls.append((job, threading.get_ident()))
        with sqlite3.connect(path, timeout=.12) as reader:
            return reader.execute("SELECT step FROM status").fetchone()[0]

    async def scenario():
        main_thread = threading.get_ident()
        assert probe.request(7, read, interval=0)
        await _until(lambda: probe.view(7) == (25, False))
        with sqlite3.connect(path) as locker:
            locker.execute("BEGIN EXCLUSIVE")
            assert probe.request(7, read, interval=0)
            for _ in range(7):
                await asyncio.sleep(.01)
                assert not probe.request(7, read, interval=0)
                assert probe.view(7)[0] == 25
            await _until(lambda: probe.view(7) == (25, True))
            locker.execute("UPDATE status SET step=75")
        assert probe.request(7, read, interval=0)
        await _until(lambda: probe.view(7) == (75, False))
        assert len(calls) == 3 and all(t != main_thread for _, t in calls)
    asyncio.run(scenario())


def test_effect_is_durably_claimed_before_send_and_is_not_repeated_after_restart(tmp_path, monkeypatch):
    path = str(tmp_path / "queue.sqlite")
    job = queue.submit_job("pipeline", "{}", db_path=path)
    queue.fetch_pending_job(db_path=path)
    queue.complete_job(job, "{}", db_path=path)
    monkeypatch.setattr(app, "claim_job_finalization_action", partial(queue.claim_job_finalization_action, db_path=path))
    monkeypatch.setattr(app, "finish_job_finalization_action", partial(queue.finish_job_finalization_action, db_path=path))
    calls = []
    def delivery():
        assert queue.get_job_finalization_actions(job, db_path=path) == {"smtp": "claimed"}
        calls.append("smtp")
        raise OSError("connection lost after delivery")
    app._perform_job_action(job, "smtp", delivery)
    app._perform_job_action(job, "smtp", delivery)
    assert calls == ["smtp"]
    monkeypatch.setitem(app.STATE, "active_job_id", None)
    monkeypatch.setattr(app, "get_job_finalization_actions", partial(queue.get_job_finalization_actions, db_path=path))
    monkeypatch.setattr(app, "get_job_finalization_details", partial(queue.get_job_finalization_details, db_path=path))
    restored = JobStatusProbe()
    restored.request(job, app._read_ui_job)
    asyncio.run(_until(lambda: restored.view(job)[0] is not None))
    assert restored.view(job)[0]["actions"] == {"smtp": "uncertain"}
    # Simulate a crash after the durable claim and before shutdown returns.
    assert queue.claim_job_finalization_action(job, "shutdown", db_path=path)
    app._perform_job_action(job, "shutdown", lambda: pytest.fail("a claimed shutdown was replayed"))
    assert queue.get_job_finalization_actions(job, db_path=path)["shutdown"] == "claimed"


@pytest.mark.parametrize("initial_claim_committed", [False, True])
def test_poll_retries_a_temporary_claim_failure_without_replaying_an_uncertain_claim(tmp_path, monkeypatch, initial_claim_committed):
    database = str(tmp_path / "queue.sqlite")
    job = queue.submit_job("pipeline", "{}", db_path=database)
    queue.fetch_pending_job(db_path=database)
    queue.complete_job(job, "{}", db_path=database)
    claims, sends = [], []
    def claim(jid, action):
        claims.append(action)
        if len(claims) == 1:
            if initial_claim_committed:
                assert queue.claim_job_finalization_action(jid, action, db_path=database)
            raise sqlite3.OperationalError("simulated transient connection failure")
        return queue.claim_job_finalization_action(jid, action, db_path=database)
    monkeypatch.setattr(app, "_CLAIM_DELAYS", (0,))
    monkeypatch.setattr(app, "_COMPLETION_STAGES", {})
    monkeypatch.setattr(app, "_PENDING_ACTIONS", {})
    monkeypatch.setattr(app.threading, "Timer", Mock())
    monkeypatch.setattr(app, "claim_job_finalization_action", claim)
    for name in ("finish_job_finalization_action", "get_job_finalization_actions", "get_job_finalization_details"):
        monkeypatch.setattr(app, name, partial(getattr(queue, name), db_path=database))
    monkeypatch.setitem(app.STATE, "active_job_id", None)
    assert not app._perform_job_action(job, "smtp", lambda: sends.append("mail") or "done")
    assert not sends
    for _ in range(3):
        state = app._read_ui_job(job)
    assert claims == ["smtp", "smtp"]
    assert sends == ([] if initial_claim_committed else ["mail"])
    assert state["actions"] == {"smtp": "claimed" if initial_claim_committed else "done"}


def test_permanent_queue_failure_stops_after_the_allowed_number_of_claim_attempts(monkeypatch):
    operation = Mock()
    claim = Mock(side_effect=sqlite3.OperationalError("database is locked"))
    monkeypatch.setattr(app, "STATE", {"_result_job_id": 31})
    monkeypatch.setattr(app, "_COMPLETION_STAGES", {})
    monkeypatch.setattr(app, "_PENDING_ACTIONS", {})
    monkeypatch.setattr(app, "_CLAIM_DELAYS", (0, 0))
    monkeypatch.setattr(app.threading, "Timer", Mock())
    monkeypatch.setattr(app, "claim_job_finalization_action", claim)
    assert not app._perform_job_action(31, "smtp", operation)
    for _ in range(8):
        app._retry_job_claims()
    assert claim.call_count == 3 and "3 încercări" in app.STATE["_job_notice"]
    operation.assert_not_called()


@pytest.mark.parametrize("code", [0, 5])
def test_shutdown_exit_status_is_persisted_before_showing_banner_or_consuming_flag(tmp_path, monkeypatch, code):
    database = str(tmp_path / "queue.sqlite")
    job = queue.submit_job("pipeline", "{}", db_path=database)
    queue.fetch_pending_job(db_path=database)
    queue.complete_job(job, "{}", db_path=database)
    for name in ("claim_job_finalization_action", "finish_job_finalization_action", "get_job_finalization_actions",
                 "get_job_finalization_details"):
        monkeypatch.setattr(app, name, partial(getattr(queue, name), db_path=database))
    monkeypatch.setattr(app, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(app, "STATE", {})
    monkeypatch.setattr(app, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(app.subprocess, "CREATE_NO_WINDOW", 0, raising=False)
    flag = tmp_path / ".shutdown_pending.flag"
    flag.touch()
    popen = app.subprocess.Popen
    def safe_child(argv, **kwargs):
        assert argv[:3] == ["shutdown", "/s", "/t"]
        assert queue.get_job_finalization_actions(job, db_path=database) == {"shutdown": "claimed"}
        return popen([sys.executable, "-c", f"print('diagnostic fixture'); raise SystemExit({code})"], **kwargs)
    monkeypatch.setattr(app.subprocess, "Popen", safe_child)
    app._perform_job_action(job, "shutdown", app._maybe_shutdown)
    status = app._job_action_snapshot(job)
    assert flag.exists() is bool(code)
    assert bool(app.STATE.get("_shutdown_initiated")) is (code == 0)
    assert status["actions"]["shutdown"] == ("failed" if code else "done")
    if code:
        assert "cod 5" in status["details"]["shutdown"] and "diagnostic fixture" in status["details"]["shutdown"]


def _isolate_queue_ui(monkeypatch, path):
    for name in ("init_job_queue", "get_active_job", "get_job_status", "get_job_finalization_actions",
                 "cancel_unstarted_job", "mark_job_finalized", "claim_job_finalization_action",
                 "finish_job_finalization_action"):
        monkeypatch.setattr(app, name, partial(getattr(queue, name), db_path=path))
    monkeypatch.setattr(app, "STATE", {})
    monkeypatch.setattr(app, "_COMPLETION_STAGES", {})
    monkeypatch.setattr(app, "is_fresh_ui_start", lambda: False)
    monkeypatch.setattr(app, "_migrate_legacy_finalized_marker", lambda: None)


@pytest.mark.parametrize("claimed_before_startup_read", [True, False])
def test_ui_startup_keeps_worker_claim_without_progress(tmp_path, monkeypatch, claimed_before_startup_read):
    path = str(tmp_path / "queue.sqlite")
    _isolate_queue_ui(monkeypatch, path)
    job = queue.submit_job("pipeline", "{}", db_path=path)
    replay, launches = [], []
    monkeypatch.setattr(app, "_recover_completed_job", lambda **kwargs: replay.append(kwargs))
    monkeypatch.setattr(app, "ensure_worker_running", lambda: launches.append(job))

    def claim():
        assert queue.fetch_pending_job(db_path=path, worker_token="owner")["id"] == job

    if claimed_before_startup_read:
        claim()
    else:
        def claim_before_cancel(job_id, reason):
            claim()
            return queue.cancel_unstarted_job(job_id, reason, db_path=path)
        monkeypatch.setattr(app, "cancel_unstarted_job", claim_before_cancel)

    app._restore_queue_on_startup()
    row = queue.get_job_status(job, db_path=path)
    assert row["status"] == queue.JOB_RUNNING and row["worker_token"] == "owner"
    assert row["progress_pct"] == 1 and not row["log_tail"]
    assert app.STATE["active_job_id"] == job and launches == [job] and not replay


def test_ui_poll_finalizes_job_that_completes_during_unstarted_timeout(tmp_path, monkeypatch):
    import time

    path = str(tmp_path / "queue.sqlite")
    _isolate_queue_ui(monkeypatch, path)
    job = queue.submit_job("pipeline", "{}", db_path=path)
    app.STATE.update(active_job_id=job, job_start_time=time.time() - 90)
    monkeypatch.setattr(app, "SETTINGS", {"shutdown_on_complete": True})
    monkeypatch.setattr(app, "_WF_CANCEL_REQUESTED", threading.Event())
    monkeypatch.setattr(app, "is_worker_running", lambda: False)
    monkeypatch.setattr(app, "decode_queue_result", lambda payload: ({"forecast": True}, {}))
    monkeypatch.setattr(app, "_result_sources_from_job", lambda row: {})
    monkeypatch.setattr(app, "_mail_snapshot_inputs", lambda payload, sources: {"enabled": True})
    monkeypatch.setattr(app, "_freeze_mail_ratings", lambda snapshot: None)
    monkeypatch.setattr(app, "_save_report_file", lambda: None)
    monkeypatch.setattr(app, "_start_walk_forward", app._finalize_pipeline)
    actions = []

    def record(action):
        current = queue.get_job_finalization_actions(job, db_path=path)
        assert current[action] == "claimed"
        actions.append(action)
        return "done"

    monkeypatch.setattr(app, "_maybe_send_results_email", lambda snapshot: record("smtp"))
    def shutdown(eligible=None):
        assert eligible is None or eligible()
        return record("shutdown")

    monkeypatch.setattr(app, "_maybe_shutdown", shutdown)

    def complete_while_ui_waits():
        assert queue.fetch_pending_job(db_path=path, worker_token="owner")["id"] == job
        assert queue.complete_job(job, "{}", db_path=path, worker_token="owner")
    monkeypatch.setattr(app, "ensure_worker_running", complete_while_ui_waits)

    snapshot = app._read_ui_job(job)
    assert snapshot["job"]["status"] == queue.JOB_COMPLETED
    assert app.STATE["active_job_id"] is None and app.STATE["_result_job_id"] == job
    assert app.STATE.get("_job_notice") is None
    asyncio.run(_until(lambda: queue.get_job_status(job, db_path=path)["ui_finalized_at"]))
    assert app._read_ui_job(job)["actions"] == {"smtp": "done", "shutdown": "done"}
    assert sorted(actions) == ["shutdown", "smtp"]


def test_timeout_only_cancels_the_exact_unclaimed_job(tmp_path, monkeypatch):
    path = str(tmp_path / "queue.sqlite")
    _isolate_queue_ui(monkeypatch, path)
    older = queue.submit_job("pipeline", "{}", db_path=path)
    job = queue.submit_job("pipeline", "{}", db_path=path)
    app.STATE.update(active_job_id=job, job_start_time=None)
    current = app._read_ui_job(job)
    assert current["job"]["status"] == queue.JOB_CANCELLED and app.STATE["active_job_id"] is None
    assert queue.get_job_status(older, db_path=path)["status"] == queue.JOB_PENDING
