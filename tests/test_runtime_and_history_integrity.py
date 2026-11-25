import datetime as dt
import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace

import pytest

import job_queue as queue
import runtime_paths
import update_csv as domestic
import update_externe as foreign
from loto_enterprise.core.lotteries import GAMES_BY_ID
from verifica_istoric import check_history


def test_runtime_directory_controls_queue_unless_db_is_explicit(tmp_path, monkeypatch):
    monkeypatch.delenv("LOTO_JOBS_DB", raising=False)
    monkeypatch.setenv("LOTO_RUNTIME_DIR", str(tmp_path))
    assert queue._default_db_path() == str(tmp_path / "loto_jobs.db")
    invalid = tmp_path / "file"
    invalid.touch()
    monkeypatch.setenv("LOTO_RUNTIME_DIR", str(invalid))
    with pytest.raises(OSError):
        runtime_paths._resolve_runtime_root()
    with pytest.raises(OSError):
        queue._default_db_path()
    monkeypatch.setenv("LOTO_JOBS_DB", str(tmp_path / "other.db"))
    assert queue._default_db_path() == str(tmp_path / "other.db")


def test_queue_respects_empty_ids_and_bounds_first_log_line(tmp_path):
    db = str(tmp_path / "jobs.db")
    first, second = [queue.submit_job("pipeline", "{}", db) for _ in range(2)]
    assert queue.fetch_pending_job(db, "owner")["id"] == first
    assert queue.cancel_pending_running_jobs(db_path=db, job_ids=[]) == 0
    assert queue.get_job_status(first, db)["status"] == queue.JOB_RUNNING
    assert queue.get_job_status(second, db)["status"] == queue.JOB_PENDING
    error = "x" * 6500 + "end"
    assert queue.fail_job(first, error, db, "owner")
    failed = queue.get_job_status(first, db)
    assert failed["log_tail"] == error[-6000:]
    assert failed["result_json"] == error
    assert queue.cancel_pending_running_jobs(db_path=db, job_ids=(first,)) == 0
    assert queue.cancel_pending_running_jobs(db_path=db) == 1


def test_replaced_queue_is_migrated_by_concurrent_callers(tmp_path):
    path = tmp_path / "jobs.db"
    queue.init_job_queue(str(path))
    legacy = tmp_path / "legacy.db"
    with sqlite3.connect(legacy) as conn:
        conn.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY, task_type TEXT, "
                     "status TEXT, config_json TEXT, result_json TEXT, progress_pct INTEGER, "
                     "log_tail TEXT, created_at TIMESTAMP)")
        conn.execute("INSERT INTO jobs (id, status) VALUES (7, 'PENDING')")
    conn.close()
    os.replace(legacy, path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(queue.init_job_queue, [str(path)] * 4))
    row = queue.fetch_pending_job(str(path), "worker")
    assert row["id"] == 7 and row["worker_token"] == "worker"
    assert row["ui_finalized_at"] is None and row["completed_at"] is None


def test_empty_and_invalid_existing_histories_are_rejected(tmp_path):
    game = GAMES_BY_ID["at_lotto"]
    path = tmp_path / game.csv
    path.parent.mkdir(parents=True)
    header = "date,n1,n2,n3,n4,n5,n6\n"
    path.write_text(header)
    assert check_history(game.csv, tmp_path) == "CSV fara extrageri"
    path.write_text(header + "01-01-2026,1,2,3,4,5,88\n02-01-2026,2,4,6,8,10,12\n")
    with pytest.raises(foreign.SourceError, match="randul 2"):
        foreign.read_csv(game, path)


def test_two_simultaneous_updates_append_the_draw_once(tmp_path, monkeypatch):
    game = GAMES_BY_ID["at_lotto"]
    today = dt.date(2026, 1, 3)
    path = tmp_path / game.csv
    path.parent.mkdir(parents=True)
    path.write_bytes(b"date,n1,n2,n3,n4,n5,n6\n02-01-2026,1,2,3,4,5,6\n")
    start = threading.Barrier(2)

    def fetch(last, as_of):
        start.wait(timeout=15)
        return [foreign.Draw(last, (1, 2, 3, 4, 5, 6)),
                foreign.Draw(as_of, (2, 4, 6, 8, 10, 12))]

    monkeypatch.setitem(foreign.SOURCES, game.game_id,
                        replace(foreign.SOURCES[game.game_id], fetch=fetch))
    with ThreadPoolExecutor(max_workers=2) as pool:
        updates = list(pool.map(lambda _: foreign.update_game(game, tmp_path, today), range(2)))
    assert sorted(count for count, _ in updates) == [0, 1]
    assert len(foreign.read_csv(game, path, today)) == 2


def test_domestic_update_rechecks_history_after_download(tmp_path, monkeypatch):
    class AuditDate(dt.date):
        @classmethod
        def today(cls):
            return cls(2026, 11, 25)

    game = domestic.GAME_CONFIGS["loto_6_49"]
    path = tmp_path / game["csv_name"]
    path.write_bytes(b"date,n1,n2,n3,n4,n5,n6\n15-11-2026,1,2,3,4,5,6\n")
    changed = path.read_bytes() + b"22-11-2026,7,8,9,10,11,12\n"

    @contextmanager
    def another_writer_finished():
        path.write_bytes(changed)
        yield

    monkeypatch.setattr(domestic, "GAME_CONFIGS", {"loto_6_49": game})
    monkeypatch.setattr(domestic, "date", AuditDate)
    monkeypatch.setattr(domestic, "_find_istoric_dir", lambda: tmp_path)
    monkeypatch.setattr(domestic, "_get_page_text", lambda _: (
        "2026-11-15 1 2 3 4 5 6 2026-11-22 40 39 38 37 36 35"
    ))
    monkeypatch.setattr(domestic, "history_write_lock", another_writer_finished)
    assert domestic.update_all() == 0
    assert path.read_bytes() == changed


def test_failed_job_is_saved_after_temporary_database_error(monkeypatch):
    import worker

    class StopLoop(BaseException):
        pass

    job = {"id": 42, "status": queue.JOB_RUNNING}
    jobs = iter([job])
    messages = []

    def poll(**kwargs):
        try:
            return next(jobs)
        except StopIteration:
            raise StopLoop

    def process(_):
        raise ValueError("bad job")

    def save(job_id, message, worker_token):
        messages.append((job_id, message, worker_token))
        if len(messages) == 1:
            raise sqlite3.OperationalError("database is locked")
        return True

    monkeypatch.setattr(worker.atexit, "register", lambda *_: None)
    monkeypatch.setattr(worker.signal, "signal", lambda *_: None)
    monkeypatch.setattr(worker.time, "sleep", lambda *_: None)
    monkeypatch.setattr(worker, "requeue_running_jobs", lambda: 0)
    monkeypatch.setattr(worker, "fetch_pending_job", poll)
    monkeypatch.setattr(worker, "_process_one_job", process)
    monkeypatch.setattr(worker, "fail_job", save)
    with pytest.raises(StopLoop):
        worker.main()
    assert len(messages) == 2 and messages[0] == messages[1]
    assert "bad job" in messages[0][1]
