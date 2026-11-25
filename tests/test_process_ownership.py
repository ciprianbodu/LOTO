"""Actual process lifetime, write ownership, and duplicate submission regressions."""

import datetime as dt
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest


ROOT = Path(__file__).resolve().parents[1]


def child(script, *args, env=None):
    return subprocess.Popen([sys.executable, "-c", script, *map(str, args)],
                            cwd=ROOT, env={**os.environ, **(env or {})},
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def until(predicate, process=None):
    deadline = time.monotonic() + 40
    while not predicate():
        if process is not None and process.poll() is not None:
            pytest.fail(process.stdout.read())
        if time.monotonic() >= deadline:
            pytest.fail("Timed out waiting for the child process")
        time.sleep(0.025)


def test_active_lock_times_out_and_recovers_after_kill(tmp_path):
    from process_lock import file_lock

    target, ready = tmp_path / "data", tmp_path / "ready"
    holder = child("from process_lock import file_lock\nimport sys,time,pathlib\n"
                   "with file_lock(sys.argv[1], timeout=0.02):\n"
                   " pathlib.Path(sys.argv[2]).touch(); time.sleep(60)", target, ready)
    try:
        until(ready.exists, holder)
        with pytest.raises(TimeoutError), file_lock(target, timeout=0.1):
            pytest.fail("Second holder entered")
    finally:
        if holder.poll() is None:
            holder.kill()
        holder.communicate(timeout=5)
    with file_lock(target, timeout=0.1):
        assert ready.exists()


def test_duplicate_workers_recover_only_after_death(tmp_path):
    import job_queue as q

    db = str(tmp_path / "queue.db")
    jid = q.submit_job("test", "{}", db_path=db)
    started = tmp_path / "started"
    env = {"LOTO_JOBS_DB": db, "LOTO_RUNTIME_DIR": str(tmp_path),
           "LOTO_WF_CACHE_DIR": str(tmp_path / "wf")}
    program = """import pathlib,sys,time
import worker,job_queue
log=pathlib.Path(sys.argv[1])
def work(job):
    with log.open("a") as stream:
        stream.write(worker.WORKER_TOKEN + "\\n")
    if sys.argv[2] == "hold":
        time.sleep(65)
    else:
        assert job_queue.complete_job(job["id"], "{}", worker_token=worker.WORKER_TOKEN)
        raise SystemExit(0)
worker._process_one_job=work
worker.main()
"""
    children = [child(program, started, "hold", env=env) for _ in range(2)]
    try:
        until(lambda: started.exists() and started.stat().st_size > 0)
        until(lambda: sum(p.poll() is not None for p in children) == 1)
        owner = next(p for p in children if p.poll() is None)
        first = started.read_text().splitlines()
        assert len(first) == 1 and q.get_job_status(jid, db)["worker_token"] == first[0]
        owner.kill()
        owner.communicate(timeout=10)
        restart = child(program, started, "finish", env=env)
        children.append(restart)
        output, _ = restart.communicate(timeout=40)
        assert restart.returncode == 0, output
        status = q.get_job_status(jid, db)
        assert status["status"] == q.JOB_COMPLETED
        assert status["log_tail"].count("reprogramat automat") == 1
        assert len(set(started.read_text().splitlines())) == 2
    finally:
        for p in children:
            if p.poll() is None:
                p.kill()
            p.communicate(timeout=10)


def test_worker_releases_ownership_after_all_failed_writes_and_restart_recovers(tmp_path):
    import sqlite3
    import job_queue as q

    db = str(tmp_path / "queue.db")
    jid = q.submit_job("unknown", "{}", db_path=db)
    ready, locked, attempts = tmp_path / "ready", tmp_path / "locked", tmp_path / "attempts"
    env = {"LOTO_JOBS_DB": db, "LOTO_RUNTIME_DIR": str(tmp_path),
           "LOTO_WF_CACHE_DIR": str(tmp_path / "wf")}
    script = """import pathlib, sys, time
import job_queue as q, worker

connect = q._connect
def short_timeout(*args):
    conn = connect(*args)
    conn.execute("PRAGMA busy_timeout=40")
    return conn
q._connect = short_timeout
worker._COMPLETE_BACKOFF_SEC = .01
fail_job = worker.fail_job
def record_failure(*args, **kwargs):
    with pathlib.Path(sys.argv[3]).open("a") as log:
        log.write("x")
    return fail_job(*args, **kwargs)
worker.fail_job = record_failure

def work(job):
    pathlib.Path(sys.argv[1]).touch()
    for _ in range(400):
        if pathlib.Path(sys.argv[2]).exists():
            raise RuntimeError("simulated job failure")
        time.sleep(.025)
    raise AssertionError("The test failed to lock SQLite")
worker._process_one_job = work
worker.main()
"""
    owner = child(script, ready, locked, attempts, env=env)
    restart = None
    try:
        until(ready.exists, owner)
        with sqlite3.connect(db) as blocker:
            blocker.execute("BEGIN IMMEDIATE")
            locked.touch()
            output, _ = owner.communicate(timeout=25)
            assert owner.returncode == 0, output
            assert attempts.read_text() == "xxxx"
            assert q.get_job_status(jid, db)["status"] == q.JOB_RUNNING
        restart = child("import worker; worker.main()", env=env)
        until(lambda: q.get_job_status(jid, db)["status"] == q.JOB_FAILED, restart)
        row = q.get_job_status(jid, db)
        assert "Unsupported task type" in row["result_json"]
        assert row["log_tail"].count("reprogramat automat") == 1
    finally:
        for proc in (owner, restart):
            if proc is not None and proc.poll() is None:
                proc.kill()
                proc.communicate(timeout=10)


def test_two_ui_processes_share_one_durable_claim(tmp_path):
    import job_queue as q

    db = str(tmp_path / "queue.db")
    jid = q.submit_job("test", "{}", db_path=db)
    q.fetch_pending_job(db_path=db, worker_token="owner")
    q.complete_job(jid, "{}", db_path=db, worker_token="owner")
    code = ("import job_queue as q,sys; "
            "print(q.claim_job_finalization_action(int(sys.argv[1]),'smtp',sys.argv[2]))")
    pair = [child(code, jid, db) for _ in range(2)]
    assert sorted(p.communicate(timeout=15)[0].strip() for p in pair) == ["False", "True"]
    assert q.get_job_finalization_actions(jid, db) == {"smtp": "claimed"}
    assert q.finish_job_finalization_action(jid, "smtp", "uncertain", db_path=db)
    assert not q.claim_job_finalization_action(jid, "smtp", db)
    assert q.get_job_finalization_actions(jid, db)["smtp"] == "uncertain"


def test_concurrent_csv_writers_preserve_distinct_special_draws(tmp_path):
    import csv
    path = tmp_path / "loto_5_40.csv"
    code = """import sys, datetime as dt
from pathlib import Path
from update_csv import _append_rows_atomic
row = {"date": dt.date(2025,1,1), "main": list(map(int, sys.argv[2].split(",")))}
print(_append_rows_atomic(Path(sys.argv[1]), [row, row], False, 6))
"""
    pair = [child(code, path, numbers) for numbers in ("1,2,3,4,5,6", "1,2,3,4,6,5")]
    for process in pair:
        output, _ = process.communicate(timeout=20)
        assert process.returncode == 0 and output.strip() == "1", output
    with path.open(newline="") as stream:
        rows = list(csv.reader(stream))
    assert len(rows) == 3 and {r[-1] for r in rows[1:]} == {"5", "6"}


def test_cancelled_owned_job_stays_cancelled_after_owner_dies(tmp_path):
    import job_queue as q

    db = str(tmp_path / "cancel.db")
    job_id = q.submit_job("test", "{}", db_path=db)
    env = {"LOTO_JOBS_DB": db, "LOTO_RUNTIME_DIR": str(tmp_path), "LOTO_WF_CACHE_DIR": str(tmp_path / "wf")}
    acquired = tmp_path / "running"
    process = child("import worker,sys,time,pathlib\n"
                    "def work(job):\n pathlib.Path(sys.argv[1]).touch(); time.sleep(60)\n"
                    "worker._process_one_job=work\nworker.main()", acquired, env=env)
    try:
        until(acquired.exists, process)
        q.cancel_pending_running_jobs(db_path=db)
        assert q.get_job_status(job_id, db)["status"] == q.JOB_CANCELLED
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)
    assert q.requeue_running_jobs(db_path=db) == 0
    assert q.fetch_pending_job(db_path=db, worker_token="restart") is None
