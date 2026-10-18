"""Regressions for queue ownership and equivalent numeric draw validation."""

import numpy as np
import pandas as pd
import pytest

import job_queue as jq
from loto_enterprise.core.draw_validation import valid_draw_matrix


@pytest.mark.parametrize("progress", [0, 1, 2, 15])
def test_late_progress_cannot_reopen_an_active_job_for_claim(tmp_path, progress):
    db = str(tmp_path / "jobs.db")
    job_id = jq.submit_job("pipeline", "{}", db_path=db)
    jq.fetch_pending_job(db_path=db, worker_token="owner")
    assert not jq.update_job_progress(
        job_id, 30, "frequency", db_path=db, worker_token="owner"
    )
    # Pipeline callbacks are not ordered by percentage (30 then 15). With
    # several tasks, their overall percentages can also round down to 0/1.
    assert not jq.update_job_progress(
        job_id, progress, "scoring", db_path=db, worker_token="owner"
    )
    status = jq.get_job_status(job_id, db_path=db)
    assert status["progress_pct"] == 30
    assert "scoring" in status["log_tail"]
    assert jq.fetch_running_job(db_path=db, worker_token="intruder") is None
    assert jq.complete_job(job_id, "result", db_path=db, worker_token="owner")


def test_requeued_job_can_restart_its_progress(tmp_path):
    db = str(tmp_path / "jobs.db")
    job_id = jq.submit_job("pipeline", "{}", db_path=db)
    jq.fetch_pending_job(db_path=db, worker_token="old")
    jq.update_job_progress(job_id, 90, "old run", db_path=db, worker_token="old")
    assert jq.requeue_running_jobs(db_path=db, worker_token="old") == 1
    claim = jq.fetch_pending_job(db_path=db, worker_token="new")
    assert claim["progress_pct"] == 1
    assert not jq.update_job_progress(
        job_id, 3, "new run", db_path=db, worker_token="new"
    )
    assert jq.get_job_status(job_id, db_path=db)["progress_pct"] == 3


@pytest.mark.parametrize(
    "dtype",
    ["int64", "uint64", "float32", "float64", "Int64", "Float64", "object", "string"],
)
def test_draw_validation_keeps_numeric_dtype_semantics(dtype):
    # Integral values are valid even in decimal/string columns; duplicates and
    # values outside the game remain invalid on every representation.
    rows = [[1, 2, 3, 4, 5, 6], [1, 2, 3, 4, 5, 5], [1, 2, 3, 4, 5, 50]]
    frame = pd.DataFrame(
        rows, columns=[f"n{i}" for i in range(1, 7)], dtype=dtype
    )
    original = frame.copy(deep=True)
    matrix, mask = valid_draw_matrix(frame, list(frame), draw_n=6, max_num=49)
    assert matrix.dtype == np.int64
    assert mask.dtype == np.bool_
    assert mask.tolist() == [True, False, False]
    assert matrix.tolist() == [rows[0]]
    pd.testing.assert_frame_equal(frame, original)


@pytest.mark.parametrize("dtype", ["float32", "float64", "Float64", "object"])
def test_numeric_validation_keeps_nonfinite_and_fractional_rejection(dtype):
    frame = pd.DataFrame(
        {"joker": [1, 20, np.nan, np.inf, -np.inf, 1.5, 0, 21]}, dtype=dtype
    )
    matrix, mask = valid_draw_matrix(frame, ["joker"], draw_n=1, max_num=20)
    assert mask.tolist() == [True, True, False, False, False, False, False, False]
    assert matrix.tolist() == [[1], [20]]


@pytest.mark.parametrize("dtype", ["Int64", "Float64"])
def test_nullable_numeric_draw_validation_rejects_missing(dtype):
    frame = pd.DataFrame({"joker": pd.Series([1, pd.NA, 20], dtype=dtype)})
    matrix, mask = valid_draw_matrix(frame, ["joker"], draw_n=1, max_num=20)
    assert mask.tolist() == [True, False, True]
    assert matrix.tolist() == [[1], [20]]


@pytest.mark.parametrize("progress", [None, 0, 1])
@pytest.mark.parametrize("intruder", [None, "other"])
def test_claim_token_protects_before_first_worker_progress(tmp_path, progress, intruder):
    db = str(tmp_path / "jobs.db")
    job_id = jq.submit_job("pipeline", "{}", db_path=db)
    jq.fetch_pending_job(db_path=db, worker_token="owner")
    if progress is not None:
        assert not jq.update_job_progress(
            job_id, progress, "early callback", db_path=db, worker_token="owner"
        )
    assert jq.fetch_running_job(db_path=db, worker_token=intruder) is None
    continued = jq.fetch_running_job(db_path=db, worker_token="owner")
    assert continued["id"] == job_id
    assert continued["worker_token"] == "owner"
    assert continued["progress_pct"] == 2


def test_running_legacy_job_without_token_can_be_claimed(tmp_path):
    db = str(tmp_path / "jobs.db")
    job_id = jq.submit_job("pipeline", "{}", db_path=db)
    jq.fetch_pending_job(db_path=db)
    resumed = jq.fetch_running_job(db_path=db, worker_token="new owner")
    assert resumed["id"] == job_id
    assert resumed["worker_token"] == "new owner"
    assert resumed["progress_pct"] == 2
    assert jq.fetch_running_job(db_path=db, worker_token="other") is None
