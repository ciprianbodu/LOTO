"""
Test pentru `reset_jobs.py` — START_8000.bat omoară worker-ul, apoi --force:

  1. Sesiune curată (nimic de recuperat) → golire COMPLETĂ + VACUUM → următorul
     job devine #1.
  2. PENDING/RUNNING rămase după kill NU se păstrează (sunt cadavre; altfel UI-ul
     arată «Job în rulare (#1) — 0% / se inițializează...» la o pornire goală).
  3. Ultimul job COMPLETED NU a fost încă procesat de UI (`last_finalized_job_id`
     diferit) → e PĂSTRAT, ca `_recover_completed_job` să-l poată afișa.
     START_8000 îl recuperează display-only; restartul direct al UI poate finaliza.

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


def test_force_survives_vacuum_failure_queue_still_cleared(isolated_db, monkeypatch):
    """VACUUM (recuperare spațiu + renumerotare) NU e o condiție de corectitudine —
    DELETE+commit de dinainte a golit deja coada cu succes. Un VACUUM eșuat (lock
    rezidual imediat după kill-ul workerului vechi, disc plin) nu are voie să
    propage o excepție care ar opri toată pornirea START_8000.bat peste o coadă
    deja corect goală."""
    completed_id = _insert_job(isolated_db, "COMPLETED", completed=True)
    monkeypatch.setattr(reset_jobs, "_last_finalized_job_id", lambda: completed_id)

    # sqlite3.Connection e un tip C imutabil — nu poate fi patch-uit direct;
    # injectam o subclasa prin `factory=` la connect().
    class _VacuumFailingConnection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if str(sql).strip().upper() == "VACUUM":
                raise sqlite3.OperationalError("database is locked")
            return super().execute(sql, *args, **kwargs)

    real_connect = sqlite3.connect

    def _connect_with_vacuum_failure(*args, **kwargs):
        kwargs["factory"] = _VacuumFailingConnection
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", _connect_with_vacuum_failure)
    monkeypatch.setattr("sys.argv", ["reset_jobs.py", "--force"])
    rc = reset_jobs.main()

    assert rc == 0
    assert _job_ids(isolated_db) == set()  # coada tot golita, in ciuda VACUUM esuat


# --------------------------------------------------------------------------- #
# Scenariul 2: leftover PENDING/RUNNING după kill → ȘTERSE (nu reapar la pornire)
# --------------------------------------------------------------------------- #


def test_force_deletes_pending_and_running_jobs(isolated_db, monkeypatch):
    """START_8000 a omorât worker-ul: PENDING/RUNNING sunt cadavre, nu muncă în curs."""
    pending_id = _insert_job(isolated_db, "PENDING")
    running_id = _insert_job(isolated_db, "RUNNING")
    old_completed_id = _insert_job(isolated_db, "COMPLETED", completed=True)
    monkeypatch.setattr(reset_jobs, "_last_finalized_job_id", lambda: old_completed_id)

    monkeypatch.setattr("sys.argv", ["reset_jobs.py", "--force"])
    rc = reset_jobs.main()

    assert rc == 0
    remaining = _job_ids(isolated_db)
    assert pending_id not in remaining
    assert running_id not in remaining
    assert old_completed_id not in remaining  # deja finalizat de UI → nu se păstrează
    assert remaining == set()


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


def test_running_check_and_delete_are_one_atomic_transaction(isolated_db, monkeypatch):
    """TOCTOU: verificarea RUNNING și DELETE-ul final trebuie să fie sub ACELAȘI
    lock de scriere, nu instrucțiuni separate. Altfel un worker poate trece un
    job PENDING în RUNNING exact în fereastra dintre ele — invizibil pentru
    gardă, dar șters oricum de `DELETE ... WHERE id NOT IN (...)`.

    `_last_finalized_job_id()` e apelat DUPĂ verificarea RUNNING, în interiorul
    aceleiași tranzacții — folosim exact acest hook, deja existent în main(),
    ca să încercăm o scriere de pe o a doua conexiune. Cu BEGIN IMMEDIATE ținut
    corect, scrierea trebuie să eșueze cu „database is locked" (busy_timeout
    scurt, ca testul să nu aștepte); fără el, ar reuși nestingherită."""
    _insert_job(isolated_db, "COMPLETED", completed=True)
    other = sqlite3.connect(isolated_db, timeout=0.2)
    attempted = {}

    def _probe_write_during_transaction():
        try:
            other.execute(
                "INSERT INTO jobs (task_type, status, config_json) VALUES (?,?,?)",
                ("pipeline", "PENDING", "{}"),
            )
            other.commit()
            attempted["locked"] = False
        except sqlite3.OperationalError as exc:
            attempted["locked"] = "locked" in str(exc).lower()
        return 0

    monkeypatch.setattr(reset_jobs, "_last_finalized_job_id", _probe_write_during_transaction)
    monkeypatch.setattr("sys.argv", ["reset_jobs.py", "--force"])
    try:
        rc = reset_jobs.main()
    finally:
        other.close()

    assert rc == 0
    assert attempted.get("locked") is True, (
        "a doua conexiune a putut scrie in timp ce reset_jobs.py verifica "
        "RUNNING si pregatea DELETE-ul — lock-ul BEGIN IMMEDIATE nu a fost "
        "tinut pe toata sectiunea critica"
    )


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
    assert fresh_id in remaining, (
        "job COMPLETED nefinalizat trebuie păstrat pentru recuperare"
    )
    assert old_id not in remaining


def test_force_drops_pending_but_keeps_unfinalized_completed(isolated_db, monkeypatch):
    """Cadavrul PENDING nu blochează recuperarea unui COMPLETED nefinalizat."""
    pending_id = _insert_job(isolated_db, "PENDING")
    completed_id = _insert_job(isolated_db, "COMPLETED", completed=True)
    monkeypatch.setattr(reset_jobs, "_last_finalized_job_id", lambda: 0)

    monkeypatch.setattr("sys.argv", ["reset_jobs.py", "--force"])
    rc = reset_jobs.main()

    assert rc == 0
    remaining = _job_ids(isolated_db)
    assert pending_id not in remaining
    assert completed_id in remaining


def test_force_ghost_pending_resets_autoincrement(isolated_db, monkeypatch):
    """Job-ul fantomă #1 (0%, fără log) nu trebuie să rămână; următorul job e iar #1."""
    ghost = _insert_job(isolated_db, "PENDING")
    assert ghost == 1
    monkeypatch.setattr(reset_jobs, "_last_finalized_job_id", lambda: 0)
    monkeypatch.setattr("sys.argv", ["reset_jobs.py", "--force"])
    reset_jobs.main()

    assert _job_ids(isolated_db) == set()
    new_id = job_queue.submit_job("pipeline", "{}", db_path=isolated_db)
    assert new_id == 1


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


def test_old_schema_is_migrated_before_completed_query(tmp_path, monkeypatch):
    """Fresh-start trebuie să repare DB-ul vechi înainte să citească completed_at."""
    db_path = str(tmp_path / "old_schema.db")
    with sqlite3.connect(db_path) as con:
        con.execute(
            "CREATE TABLE jobs (id INTEGER PRIMARY KEY, task_type TEXT NOT NULL, "
            "status TEXT NOT NULL, config_json TEXT NOT NULL, result_json TEXT, "
            "progress_pct INTEGER NOT NULL DEFAULT 0, log_tail TEXT NOT NULL DEFAULT '', "
            "created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
    job_queue._INITIALIZED_DBS.discard(db_path)
    monkeypatch.setattr(reset_jobs, "DB", db_path)
    monkeypatch.setattr("sys.argv", ["reset_jobs.py", "--force"])

    assert reset_jobs.main() == 0
    with sqlite3.connect(db_path) as con:
        cols = {row[1] for row in con.execute("PRAGMA table_info(jobs)")}
    assert "completed_at" in cols


# --------------------------------------------------------------------------- #
# is_stale_unstarted_job — garda din _startup
# --------------------------------------------------------------------------- #


def test_stale_unstarted_pending_without_worker():
    job = {"status": "PENDING", "progress_pct": 0, "log_tail": ""}
    assert job_queue.is_stale_unstarted_job(job, worker_alive=False) is True
    job["progress_pct"] = 1  # fetch_pending_job pune 1 la claim; tot e nepornit
    assert job_queue.is_stale_unstarted_job(job, worker_alive=False) is True


def test_unstarted_even_if_worker_alive():
    """La boot, 0% fără log e leftover — worker viu nu-l face job real."""
    job = {"status": "PENDING", "progress_pct": 0, "log_tail": ""}
    assert job_queue.is_unstarted_job(job) is True
    assert job_queue.is_stale_unstarted_job(job, worker_alive=True) is True


def test_started_job_with_worker_log_is_not_unstarted():
    job = {
        "status": "RUNNING",
        "progress_pct": 2,
        "log_tail": "[12:00] Job preluat de worker.",
    }
    assert job_queue.is_unstarted_job(job) is False
    assert job_queue.is_stale_unstarted_job(job, worker_alive=False) is False


def test_not_stale_when_running_with_progress():
    job = {"status": "RUNNING", "progress_pct": 40, "log_tail": "[12:00] scoring"}
    assert job_queue.is_stale_unstarted_job(job, worker_alive=False) is False


def test_not_stale_when_pending_has_log():
    job = {
        "status": "PENDING",
        "progress_pct": 0,
        "log_tail": "Worker restart detectat: job reprogramat automat.",
    }
    assert job_queue.is_stale_unstarted_job(job, worker_alive=False) is False


def test_fresh_ui_start_flag(monkeypatch):
    monkeypatch.delenv("LOTO_FRESH_START", raising=False)
    assert job_queue.is_fresh_ui_start() is False
    monkeypatch.setenv("LOTO_FRESH_START", "1")
    assert job_queue.is_fresh_ui_start() is True
    monkeypatch.setenv("LOTO_FRESH_START", "true")
    assert job_queue.is_fresh_ui_start() is True
    monkeypatch.setenv("LOTO_FRESH_START", "no")
    assert job_queue.is_fresh_ui_start() is False


def test_fresh_start_cancels_leftover_pending(isolated_db, monkeypatch):
    """START_8000: leftover PENDING e anulat, nu e reluat ca job nou."""
    jid = _insert_job(isolated_db, "PENDING")
    n = job_queue.cancel_pending_running_jobs(
        "Pornire START_8000: sesiune nouă, fără job automat.",
        db_path=isolated_db,
    )
    assert n == 1
    assert job_queue.get_active_job(db_path=isolated_db) is None
    st = job_queue.get_job_status(jid, db_path=isolated_db)
    assert st["status"] == "CANCELLED"
    """După --force pe un cadavru PENDING, UI-ul nu mai are ce reatașa."""
    _insert_job(isolated_db, "PENDING")
    monkeypatch.setattr("sys.argv", ["reset_jobs.py", "--force"])
    reset_jobs.main()
    assert job_queue.get_active_job(db_path=isolated_db) is None
