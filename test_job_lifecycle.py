"""Contractul cozii de joburi, pe TOATE ramurile — inclusiv cele de eroare.

Golul pe care îl acoperă: până acum nu exista niciun test pe `worker.py` sau pe
tranzițiile de stare ale cozii, deși acolo trăiesc exact defectele care au trecut
neobservate (rezultat pierdut tăcut, job „reușit" fără date, anulare care lovea
joburi străine). Fiecare test merge pe o RAMURĂ, nu pe calea fericită.

⚠️ `db_path=DB_PATH` din `job_queue` e evaluat la DEF-time: a repune
`job_queue.DB_PATH` NU redirecționează apelurile deja legate. De aceea testele
pasează `db_path` explicit, iar cele pe `worker` leagă funcțiile cu partial.
"""
import functools
import json

import pytest

import job_queue as jq
from ui_shared import decode_queue_result, pack_queue_result


@pytest.fixture()
def db(tmp_path):
    return str(tmp_path / "jobs.db")


def _payload():
    return pack_queue_result(([("f.csv", {"6/49": {"variants": [[1, 2, 3, 4, 5, 6]]}})], 1))


# --- calea fericită, ca ancoră -------------------------------------------------
def test_submit_claim_complete_roundtrip(db):
    jid = jq.submit_job("pipeline", "{}", db_path=db)
    claimed = jq.fetch_pending_job(db_path=db)
    assert claimed and claimed["id"] == jid
    assert jq.complete_job(jid, _payload(), db_path=db)
    st = jq.get_job_status(jid, db_path=db)
    assert st["status"] == "COMPLETED"
    assert isinstance(decode_queue_result(st["result_json"]), tuple)


# --- concurență: doi workeri ---------------------------------------------------
def test_pending_job_claimed_only_once(db):
    jq.submit_job("pipeline", "{}", db_path=db)
    first = jq.fetch_pending_job(db_path=db)
    second = jq.fetch_pending_job(db_path=db)
    assert first is not None and second is None


def test_running_job_not_stolen_by_second_worker(db):
    """`fetch_running_job` e calea de resume; nu are voie să dea același job de 2×."""
    jq.submit_job("pipeline", "{}", db_path=db)
    jq.fetch_pending_job(db_path=db)
    jq.requeue_running_jobs(db_path=db)
    jq.fetch_pending_job(db_path=db)      # claim → progress 1
    a = jq.fetch_running_job(db_path=db)
    b = jq.fetch_running_job(db_path=db)
    assert not (a and b and a["id"] == b["id"])


# --- worker mort ---------------------------------------------------------------
def test_dead_worker_job_is_requeued_and_resumed(db):
    jid = jq.submit_job("pipeline", "{}", db_path=db)
    jq.fetch_pending_job(db_path=db)
    assert jq.requeue_running_jobs(db_path=db) == 1
    again = jq.fetch_pending_job(db_path=db)
    assert again and again["id"] == jid


# --- rezultatul nu se pierde și nu se suprascrie --------------------------------
def test_complete_on_non_running_job_is_refused(db):
    """Cursa cu un requeue concurent: rezultatul NU are voie să învie un CANCELLED."""
    jid = jq.submit_job("pipeline", "{}", db_path=db)
    jq.fetch_pending_job(db_path=db)
    jq.cancel_pending_running_jobs(db_path=db, job_ids=[jid])
    assert not jq.complete_job(jid, _payload(), db_path=db)
    assert jq.get_job_status(jid, db_path=db)["status"] == "CANCELLED"


def test_fail_cannot_destroy_a_completed_result(db):
    jid = jq.submit_job("pipeline", "{}", db_path=db)
    jq.fetch_pending_job(db_path=db)
    jq.complete_job(jid, _payload(), db_path=db)
    assert not jq.fail_job(jid, "boom", db_path=db)
    st = jq.get_job_status(jid, db_path=db)
    assert st["status"] == "COMPLETED"
    assert isinstance(decode_queue_result(st["result_json"]), tuple)


# --- anularea nu are voie să lovească joburi străine ----------------------------
def test_cancel_with_ids_does_not_touch_other_jobs(db):
    a = jq.submit_job("pipeline", "{}", db_path=db)
    b = jq.submit_job("pipeline", "{}", db_path=db)
    jq.cancel_pending_running_jobs(db_path=db, job_ids=[a])
    assert jq.get_job_status(a, db_path=db)["status"] == "CANCELLED"
    assert jq.get_job_status(b, db_path=db)["status"] == "PENDING"


# --- job dispărut sub worker ----------------------------------------------------
def test_missing_job_signals_stop_not_crash(db):
    """Jobul șters (reset_jobs) în timp ce worker-ul lucrează = STOP, nu excepție."""
    assert jq.is_job_cancelled(999_999, db_path=db) is True
    assert jq.update_job_progress(999_999, 50, "x", db_path=db) is True


# --- worker: date inutilizabile nu produc „succes" ------------------------------
def test_worker_marks_job_failed_when_config_has_no_datasets(db):
    import worker

    jid = jq.submit_job("pipeline", json.dumps({"datasets": []}), db_path=db)
    jq.fetch_pending_job(db_path=db)
    _fail, _prog = worker.fail_job, worker.update_job_progress
    worker.fail_job = functools.partial(jq.fail_job, db_path=db)
    worker.update_job_progress = functools.partial(jq.update_job_progress, db_path=db)
    try:
        assert worker._run_pipeline_job(jq.get_job_status(jid, db_path=db)) is None
    finally:
        worker.fail_job, worker.update_job_progress = _fail, _prog
    assert jq.get_job_status(jid, db_path=db)["status"] == "FAILED"
