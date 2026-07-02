"""
Test pentru `reset_jobs.py` — cele 3 scenarii introduse în commit fda5948
("reset_jobs: păstrează munca utilă la --force"):

  1. Sesiune curată (nimic PENDING/RUNNING, ultimul COMPLETED deja finalizat
     de UI) → golire COMPLETĂ + VACUUM → următorul job devine #1.
  2. Există joburi PENDING/RUNNING → sunt PĂSTRATE (nu se pierde munca în curs).
  3. Ultimul job COMPLETED NU a fost încă finalizat de UI (`last_finalized_job_id`
     diferit) → e PĂSTRAT, ca `_recover_completed_job` din app_nicegui.py să
     poată încă trimite mail/shutdown pentru el.

Plus: fără --force, refuză ștergerea dacă există joburi RUNNING.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

import job_queue
import reset_jobs


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """DB SQLite izolat per test — schema reală creată prin job_queue.init_job_queue,
    ca testul să rămână valid dacă schema evoluează (nu o duplicăm manual)."""
    db_path = str(tmp_path / "loto_jobs_test.db")
    job_queue._INITIALIZED_DBS.discard(db_path)
    job_queue.init_job_queue(db_path)
    monkeypatch.setattr(reset_jobs, "DB", db_path)
    # Izolăm și .ui_state.json — nu citim/scriem starea reală a UI-ului din proiect.
    monkeypatch.setattr(reset_jobs, "_last_finalized_job_id", lambda: 0)
    yield db_path


def _insert_job(db_path: str, status: str, completed: bool = False) -> int:
    with sqlite3.connect(db_path) as con:
        cur = con.execute(
            "INSERT INTO jobs (task_type, status, config_json, completed_at) "
            "VALUES (?, ?, ?, ?)",
            ("pipeline", status, "{}", "2026-07-01 09:00:00" if completed else None),
        )
        con.commit()
        return int(cur.lastrowid)


def _job_ids(db_path: str) -> set[int]:
    with sqlite3.connect(db_path) as con:
        return {int(r[0]) for r in con.execute("SELECT id FROM jobs")}


# --------------------------------------------------------------------------- #
# Scenariul 1: sesiune curată → golire completă + VACUUM
# --------------------------------------------------------------------------- #

def test_force_clean_session_deletes_everything(isolated_db, monkeypatch):
    monkeypatch.setattr(reset_jobs, "_last_finalized_job_id", lambda: 1)
    jid = _insert_job(isolated_db, "COMPLETED", completed=True)
    assert jid == 1

    monkeypatch.setattr("sys.argv", ["reset_jobs.py", "--force"])
    rc = reset_jobs.main()

    assert rc == 0
    assert _job_ids(isolated_db) == set()


def test_force_clean_session_resets_autoincrement(isolated_db, monkeypatch):
    """După golire completă, next id trebuie să fie #1 (VACUUM + tabelă goală)."""
    completed_id = _insert_job(isolated_db, "COMPLETED", completed=True)
    # Marcăm explicit acest job ca deja finalizat de UI → nu califică pentru păstrare.
    monkeypatch.setattr(reset_jobs, "_last_finalized_job_id", lambda: completed_id)
    monkeypatch.setattr("sys.argv", ["reset_jobs.py", "--force"])
    reset_jobs.main()

    new_id = job_queue.submit_job("pipeline", "{}", db_path=isolated_db)
    assert new_id == 1


# --------------------------------------------------------------------------- #
# Scenariul 2: joburi PENDING/RUNNING → păstrate
# --------------------------------------------------------------------------- #

def test_force_keeps_pending_and_running_jobs(isolated_db, monkeypatch):
    pending_id = _insert_job(isolated_db, "PENDING")
    running_id = _insert_job(isolated_db, "RUNNING")
    old_completed_id = _insert_job(isolated_db, "COMPLETED", completed=True)
    monkeypatch.setattr(reset_jobs, "_last_finalized_job_id", lambda: old_completed_id)

    monkeypatch.setattr("sys.argv", ["reset_jobs.py", "--force"])
    rc = reset_jobs.main()

    assert rc == 0
    remaining = _job_ids(isolated_db)
    assert pending_id in remaining
    assert running_id in remaining
    assert old_completed_id not in remaining  # deja finalizat de UI → nu se păstrează


def test_without_force_refuses_when_running_present(isolated_db, monkeypatch):
    running_id = _insert_job(isolated_db, "RUNNING")

    monkeypatch.setattr("sys.argv", ["reset_jobs.py"])
    rc = reset_jobs.main()

    assert rc == 1  # refuză, nu șterge nimic
    assert running_id in _job_ids(isolated_db)


def test_without_force_succeeds_when_no_running(isolated_db, monkeypatch):
    completed_id = _insert_job(isolated_db, "COMPLETED", completed=True)
    # Marcăm explicit acest job ca deja finalizat de UI → nu califică pentru păstrare.
    monkeypatch.setattr(reset_jobs, "_last_finalized_job_id", lambda: completed_id)

    monkeypatch.setattr("sys.argv", ["reset_jobs.py"])
    rc = reset_jobs.main()

    assert rc == 0
    assert completed_id not in _job_ids(isolated_db)


# --------------------------------------------------------------------------- #
# Scenariul 3: ultimul COMPLETED nefinalizat de UI → păstrat pentru recuperare
# --------------------------------------------------------------------------- #

def test_force_keeps_latest_completed_job_if_not_finalized(isolated_db, monkeypatch):
    old_id = _insert_job(isolated_db, "COMPLETED", completed=True)
    fresh_id = _insert_job(isolated_db, "COMPLETED", completed=True)
    # UI a finalizat doar job-ul vechi — cel proaspăt e încă „neprocesat".
    monkeypatch.setattr(reset_jobs, "_last_finalized_job_id", lambda: old_id)

    monkeypatch.setattr("sys.argv", ["reset_jobs.py", "--force"])
    rc = reset_jobs.main()

    assert rc == 0
    remaining = _job_ids(isolated_db)
    assert fresh_id in remaining, "job COMPLETED nefinalizat trebuie păstrat pentru recuperare"
    assert old_id not in remaining


def test_force_deletes_completed_job_already_finalized(isolated_db, monkeypatch):
    completed_id = _insert_job(isolated_db, "COMPLETED", completed=True)
    monkeypatch.setattr(reset_jobs, "_last_finalized_job_id", lambda: completed_id)

    monkeypatch.setattr("sys.argv", ["reset_jobs.py", "--force"])
    rc = reset_jobs.main()

    assert rc == 0
    assert completed_id not in _job_ids(isolated_db)


def test_no_db_file_returns_zero_without_touching_anything(tmp_path, monkeypatch):
    missing_db = str(tmp_path / "does_not_exist.db")
    monkeypatch.setattr(reset_jobs, "DB", missing_db)
    monkeypatch.setattr("sys.argv", ["reset_jobs.py", "--force"])

    rc = reset_jobs.main()

    assert rc == 0
