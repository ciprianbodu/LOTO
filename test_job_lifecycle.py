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
    return pack_queue_result(
        ([("f.csv", {"6/49": {"variants": [[1, 2, 3, 4, 5, 6]]}})], 1)
    )


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
    jq.fetch_pending_job(db_path=db)  # claim → progress 1
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


def test_progress_cannot_overwrite_a_cancelled_job(db):
    """Cancelul câștigă cursa: progresul tardiv nu-i rescrie procentul sau logul."""
    jid = jq.submit_job("pipeline", "{}", db_path=db)
    jq.fetch_pending_job(db_path=db)
    jq.update_job_progress(jid, 25, "lucrez", db_path=db)
    jq.cancel_pending_running_jobs("stop acum", db_path=db, job_ids=[jid])
    cancelled = jq.get_job_status(jid, db_path=db)

    assert jq.update_job_progress(jid, 80, "mesaj tardiv", db_path=db) is True
    after = jq.get_job_status(jid, db_path=db)
    assert after["status"] == "CANCELLED"
    assert after["progress_pct"] == cancelled["progress_pct"] == 25
    assert after["log_tail"] == cancelled["log_tail"]
    assert "stop acum" in after["log_tail"]
    assert "mesaj tardiv" not in after["log_tail"]


def test_progress_stops_when_job_was_requeued(db):
    jid = jq.submit_job("pipeline", "{}", db_path=db)
    jq.fetch_pending_job(db_path=db)
    assert jq.requeue_running_jobs(db_path=db) == 1
    assert jq.update_job_progress(jid, 50, "worker vechi", db_path=db) is True
    after = jq.get_job_status(jid, db_path=db)
    assert after["status"] == "PENDING"
    assert after["progress_pct"] == 1
    assert "worker vechi" not in after["log_tail"]


# --- worker: date inutilizabile nu produc „succes" ------------------------------
def test_worker_marks_job_failed_when_config_has_no_datasets(db):
    import worker

    jid = jq.submit_job("pipeline", json.dumps({"datasets": []}), db_path=db)
    # worker_token trebuie să coincidă cu cel pe care worker._run_pipeline_job îl va
    # folosi mai jos (WORKER_TOKEN al modulului) — altfel garda de proprietate din
    # fail_job respinge scrierea (rândul revendicat rămâne cu worker_token=None).
    jq.fetch_pending_job(db_path=db, worker_token=worker.WORKER_TOKEN)
    _fail, _prog = worker.fail_job, worker.update_job_progress
    worker.fail_job = functools.partial(jq.fail_job, db_path=db)
    worker.update_job_progress = functools.partial(jq.update_job_progress, db_path=db)
    try:
        assert worker._run_pipeline_job(jq.get_job_status(jid, db_path=db)) is None
    finally:
        worker.fail_job, worker.update_job_progress = _fail, _prog
    assert jq.get_job_status(jid, db_path=db)["status"] == "FAILED"


# --- worker_token: un worker STALE nu mai poate suprascrie rularea care i-a luat locul ---
def test_complete_job_with_stale_worker_token_does_not_clobber_reclaimed_job(db):
    """Reproduce scenariul găsit la review: worker A revendică jobul, apoi
    requeue_running_jobs() (ex. la pornirea worker-ului B) îl trimite înapoi la
    PENDING, B îl revendică din nou și pornește o rulare NOUĂ — rezultatul STALE
    al lui A nu are voie să devină rezultatul final al jobului."""
    jid = jq.submit_job("pipeline", "{}", db_path=db)
    claimed = jq.fetch_pending_job(db_path=db, worker_token="worker-A")
    assert claimed["worker_token"] == "worker-A"

    assert jq.requeue_running_jobs(db_path=db) == 1  # ex. worker B pornește
    reclaimed = jq.fetch_pending_job(db_path=db, worker_token="worker-B")
    assert reclaimed["id"] == jid and reclaimed["worker_token"] == "worker-B"

    # A, neștiind că a pierdut proprietatea, termină și încearcă să scrie rezultatul.
    ok = jq.complete_job(jid, '{"stale":"A"}', db_path=db, worker_token="worker-A")
    assert ok is False
    after_a = jq.get_job_status(jid, db_path=db)
    assert after_a["status"] == "RUNNING"  # neatins de A
    assert after_a["result_json"] is None

    # B, proprietarul curent, termină legitim.
    assert (
        jq.complete_job(jid, '{"real":"B"}', db_path=db, worker_token="worker-B")
        is True
    )
    after_b = jq.get_job_status(jid, db_path=db)
    assert after_b["status"] == "COMPLETED"
    assert after_b["result_json"] == '{"real":"B"}'


def test_fail_job_with_stale_worker_token_does_not_clobber_reclaimed_job(db):
    """Aceeași cursă ca mai sus, dar A crapă (excepție) în loc să termine cu succes —
    fail_job al lui A nu are voie să distrugă rularea legitimă a lui B."""
    jid = jq.submit_job("pipeline", "{}", db_path=db)
    jq.fetch_pending_job(db_path=db, worker_token="worker-A")
    jq.requeue_running_jobs(db_path=db)
    jq.fetch_pending_job(db_path=db, worker_token="worker-B")

    ok = jq.fail_job(
        jid,
        "worker A a crăpat, fără legătură cu rularea B",
        db_path=db,
        worker_token="worker-A",
    )
    assert ok is False
    after = jq.get_job_status(jid, db_path=db)
    assert after["status"] == "RUNNING"  # B rămâne neatins


def test_update_job_progress_with_stale_worker_token_is_noop(db):
    jid = jq.submit_job("pipeline", "{}", db_path=db)
    jq.fetch_pending_job(db_path=db, worker_token="worker-A")
    jq.requeue_running_jobs(db_path=db)
    jq.fetch_pending_job(db_path=db, worker_token="worker-B")

    # True = "oprește-te" (semantica pentru un caller care nu mai deține jobul).
    assert (
        jq.update_job_progress(
            jid, 50, "progres stale de la A", db_path=db, worker_token="worker-A"
        )
        is True
    )
    after = jq.get_job_status(jid, db_path=db)
    assert "progres stale de la A" not in (after["log_tail"] or "")

    assert (
        jq.update_job_progress(
            jid, 50, "progres real de la B", db_path=db, worker_token="worker-B"
        )
        is False
    )
    after = jq.get_job_status(jid, db_path=db)
    assert "progres real de la B" in after["log_tail"]


def test_requeue_running_jobs_scoped_to_worker_token_leaves_other_workers_alone(db):
    """`_requeue_on_terminate` din worker.py trece worker_token=WORKER_TOKEN — oprirea
    unui worker nu are voie să smulgă jobul RUNNING legitim al altui worker viu."""
    jid_a = jq.submit_job("pipeline", "{}", db_path=db)
    jid_b = jq.submit_job("pipeline", "{}", db_path=db)
    jq.fetch_pending_job(db_path=db, worker_token="worker-A")
    jq.fetch_pending_job(db_path=db, worker_token="worker-B")

    # Worker A se oprește gracios: doar jobul LUI se reprogramează.
    assert jq.requeue_running_jobs(db_path=db, worker_token="worker-A") == 1
    status_a = jq.get_job_status(jid_a, db_path=db)
    status_b = jq.get_job_status(jid_b, db_path=db)
    assert status_a["status"] == "PENDING"
    assert status_b["status"] == "RUNNING"  # B neatins, deși era tot RUNNING


def test_requeue_running_jobs_caps_log_tail_growth(db):
    """`requeue_running_jobs` trebuie să trunchieze `log_tail` la fel ca celelalte
    UPDATE-uri de stare (update_job_progress, cancel_pending_running_jobs,
    fail_running_jobs) — altfel un job reprogramat repetat (buclă crash/restart)
    crește log_tail nemărginit."""
    jid = jq.submit_job("pipeline", "{}", db_path=db)
    jq.fetch_pending_job(db_path=db)
    with jq._conn(db) as conn:
        conn.execute("UPDATE jobs SET log_tail = ? WHERE id = ?", ("x" * 7000, jid))
        conn.commit()
    jq.requeue_running_jobs(db_path=db)
    after = jq.get_job_status(jid, db_path=db)
    assert len(after["log_tail"]) <= 6000
