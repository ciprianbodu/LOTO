"""Characterization tests for AUDIT_2026-09-14 etapa 0 fixes."""

from __future__ import annotations

import pandas as pd

import job_queue as jq
from loto_enterprise.core.history import history_dates


def test_history_parses_iso_and_dmy():
    iso = pd.DataFrame({"date": ["2026-01-02", "2026-02-03"]})
    dmy = pd.DataFrame({"date": ["02-01-2026", "03-02-2026"]})
    a = history_dates(iso)
    b = history_dates(dmy)
    assert a is not None and b is not None
    assert list(a.dt.month) == [1, 2]
    assert list(b.dt.month) == [1, 2]
    assert list(a.dt.day) == [2, 3]
    assert list(b.dt.day) == [2, 3]


def test_requeue_clears_worker_token(tmp_path):
    db = str(tmp_path / "jobs.db")
    jid = jq.submit_job("pipeline", "{}", db_path=db)
    claimed = jq.fetch_pending_job(db_path=db, worker_token="old-token")
    assert claimed and claimed["id"] == jid
    assert jq.requeue_running_jobs(db_path=db) == 1
    st = jq.get_job_status(jid, db_path=db)
    assert st["status"] == "PENDING"
    assert not st.get("worker_token")


def test_fail_job_appends_log(tmp_path):
    db = str(tmp_path / "jobs.db")
    jid = jq.submit_job("pipeline", "{}", db_path=db)
    jq.fetch_pending_job(db_path=db, worker_token="tok")
    jq.update_job_progress(jid, 10, "pornit", db_path=db, worker_token="tok")
    assert jq.fail_job(jid, "boom", db_path=db, worker_token="tok")
    st = jq.get_job_status(jid, db_path=db)
    assert st["status"] == "FAILED"
    assert "pornit" in (st.get("log_tail") or "")
    assert "boom" in (st.get("log_tail") or "")


def test_covering_greedy_reexport():
    from covering.greedy import generate_combinatorial_wheel as a
    from loto_engine import generate_combinatorial_wheel as b

    assert a is b
    wheel, cov = a([1, 2, 3, 4, 5, 6], pick=5, guarantee=3, max_variants=0)
    assert wheel
    assert cov == 100.0
