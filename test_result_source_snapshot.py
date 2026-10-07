"""The result is validated on the persisted job input, not a replacement upload."""

import functools
import io
import json

import pandas as pd
import pytest

import app_nicegui as app
import job_queue as queue
from scripts.analysis.audit_output import capture_ui
from ui_shared import pack_queue_result
from loto_enterprise.core import walk_forward_adapter as wf


class InlineThread:
    def __init__(self, target, **kwargs):
        self.target = target
    def start(self):
        self.target()


def original_input():
    return pd.DataFrame({
        "date": ["24-09-2026", "27-09-2026"],
        **{f"n{i}": [i, i + 10] for i in range(1, 7)},
    })


def config_for(frame, fname="loto_6_49.csv"):
    return json.dumps({"datasets": [{
        "fname": fname, "df_json": frame.to_json(orient="split"),
        "tasks": [{"game_label": "6/49", "country": "RO"}],
    }]})


@pytest.fixture
def isolated_result_state(monkeypatch):
    for key, value in {
        "results": None, "result_sources": None, "results_recovered": None,
        "active_job_id": None, "datasets": [], "retro": {}, "retro_meta": {},
        "wf_seq": 0, "wf_user_cancel": False, "job_start_time": None,
    }.items():
        monkeypatch.setitem(app.STATE, key, value)
    for name in ("_save_settings", "_save_report_file", "_maybe_send_results_email",
                 "_shutdown_banner", "_finalize_pipeline"):
        monkeypatch.setattr(app, name, lambda: None)
    monkeypatch.setattr(app.threading, "Thread", InlineThread)
    monkeypatch.setattr(app.results_panel, "refresh", lambda: None)
    monkeypatch.setattr(app, "_bench_running", lambda: False)


def test_live_completion_walk_forward_uses_the_submitted_queue_snapshot(
    tmp_path, monkeypatch, isolated_result_state
):
    original = original_input()
    replacement = original.copy()
    replacement["n1"] = [40, 41]
    database = str(tmp_path / "jobs.db")
    jid = queue.submit_job("pipeline", config_for(original), db_path=database)
    queue.fetch_pending_job(db_path=database)
    data = {"hard_core": list(range(1, 12)), "pool_size": 11, "guarantee": 4}
    queue.complete_job(jid, pack_queue_result(([("loto_6_49.csv", {"6/49": data})], 1)),
                       db_path=database)
    monkeypatch.setitem(app.STATE, "datasets", [("loto_6_49.csv", replacement)])
    monkeypatch.setitem(app.STATE, "active_job_id", jid)
    monkeypatch.setattr(app, "get_job_status", functools.partial(queue.get_job_status, db_path=database))
    monkeypatch.setattr(app, "mark_job_finalized",
                        functools.partial(queue.mark_job_finalized, db_path=database))
    received = []
    def run_wf(**kwargs):
        received.append(kwargs["df_source"].copy())
        return [], {"partial": False, "n_test_draws": 0, "n_expected": 0}
    monkeypatch.setattr(wf, "run_honest_walk_forward", run_wf)
    with capture_ui():
        app.status_panel.func()
    expected = pd.read_json(
        io.StringIO(original.to_json(orient="split")), orient="split", convert_dates=False
    )
    assert len(received) == 1
    pd.testing.assert_frame_equal(received[0], expected)
    assert received[0].attrs["game_id"] == "6/49"
    assert app._result_source("loto_6_49.csv") is not replacement
    assert app._last_csv_draw("loto_6_49.csv")[1] == list(range(11, 17))
    assert queue.get_job_status(jid, db_path=database)["ui_finalized_at"]


def test_recovered_result_keeps_source_even_after_another_upload(
    tmp_path, monkeypatch, isolated_result_state
):
    original = original_input()
    database = str(tmp_path / "jobs.db")
    jid = queue.submit_job("pipeline", config_for(original), db_path=database)
    queue.fetch_pending_job(db_path=database)
    queue.complete_job(jid, pack_queue_result(([("loto_6_49.csv", {"6/49": {}})], 1)),
                       db_path=database)
    monkeypatch.setattr(app, "get_latest_completed_job",
                        functools.partial(queue.get_latest_completed_job, db_path=database))
    monkeypatch.setattr(app, "mark_job_finalized",
                        functools.partial(queue.mark_job_finalized, db_path=database))
    app._recover_completed_job(allow_finalize=False)
    assert queue.get_job_status(jid, db_path=database)["ui_finalized_at"]
    monkeypatch.setitem(app.STATE, "datasets", [("loto_6_49.csv", pd.DataFrame({"n1": [49]}))])
    assert app._result_source("loto_6_49.csv")["n1"].tolist() == [1, 11]
    assert app._result_source("other.csv") is None


@pytest.mark.parametrize("raw", ["{bad", "[]", '{"datasets":[{"fname":"a","df_json":"bad"}]}'])
def test_invalid_job_snapshot_never_falls_back_to_the_replacement(raw, monkeypatch):
    sources = app._result_sources_from_job({"config_json": raw})
    monkeypatch.setitem(app.STATE, "result_sources", sources)
    monkeypatch.setitem(app.STATE, "datasets", [("a", original_input())])
    assert sources == {}
    assert app._result_source("a") is None


def test_legacy_result_without_job_metadata_can_still_use_live_source(monkeypatch):
    frame = original_input()
    monkeypatch.setitem(app.STATE, "result_sources", app._result_sources_from_job({}))
    monkeypatch.setitem(app.STATE, "datasets", [("a", frame)])
    assert app._result_source("a") is frame


def test_output_auditor_uses_original_uploaded_source_without_registered_csv(tmp_path, monkeypatch):
    from scripts.analysis import audit_output
    source = original_input()
    source.attrs.update(game_id="6/49", country="RO")
    monkeypatch.setattr(audit_output, "ROOT", tmp_path)
    lottery, frame = audit_output.audit_history("custom-upload.csv", "6/49", {}, source)
    assert lottery.game_id == "6/49"
    assert frame["n1"].tolist() == [1, 11]


def test_output_auditor_rejects_snapshot_with_another_game_identity():
    from scripts.analysis import audit_output
    source = original_input()
    source.attrs.update(game_id="at_lotto", country="AT")
    with pytest.raises(ValueError, match="altui joc"):
        audit_output.audit_history("custom.csv", "6/49", {}, source)


@pytest.mark.parametrize("reverse", [False, True])
def test_snapshot_preserves_day_first_dates_exactly_like_worker(reverse):
    from loto_enterprise.core.history import chronological_history, training_cutoffs

    # Toate zilele/lunile <=12: inferența month-first poate părea validă, dar
    # inversează primele două zile și schimbă datele/cutoff-urile WF.
    submitted = pd.DataFrame({
        "date": ["01-02-2026", "02-01-2026", "03-04-2026", "02-01-2026"],
        **{f"n{i}": [i, i + 6, i + 12, i + 18] for i in range(1, 7)},
    })
    if reverse:
        submitted = submitted.iloc[::-1].reset_index(drop=True)
    config = config_for(submitted)
    dataset = json.loads(config)["datasets"][0]
    worker_frame = pd.read_json(
        io.StringIO(str(dataset["df_json"])), orient="split", convert_dates=False
    )
    sources = app._result_sources_from_job({"config_json": config})
    ui_frame = sources["loto_6_49.csv"]
    pd.testing.assert_frame_equal(ui_frame, worker_frame)
    assert ui_frame["date"].tolist() == submitted["date"].tolist()

    worker_ordered = chronological_history(worker_frame)
    ui_ordered = chronological_history(ui_frame)
    pd.testing.assert_frame_equal(ui_ordered, worker_ordered)
    assert ui_ordered["date"].tolist() == [
        "02-01-2026", "02-01-2026", "01-02-2026", "03-04-2026"
    ]
    assert training_cutoffs(ui_ordered) == training_cutoffs(worker_ordered) == (0, 0, 2, 3)


@pytest.mark.parametrize("reverse", [False, True])
def test_output_auditor_uses_validated_draw_indices_without_mutating_source(reverse):
    from loto_enterprise.core.lotteries import require_lottery
    from scripts.analysis.audit_output import audit_cached_hits

    source = pd.DataFrame({
        "date": ["01-01-2026", "02-01-2026", "03-01-2026"],
        **{f"n{i}": [i, 1 if i < 3 else i, i + 6] for i in range(1, 7)},
    })
    if reverse:
        source = source.iloc[::-1].reset_index(drop=True)
    before = source.copy(deep=True)
    source_hash = wf._csv_hash(source, "6/49")
    # După ordonare și eliminarea rândului cu două valori 1, ținta este la
    # indexul 1 în backtester, nu la indexul 2 din snapshot-ul brut.
    flat = [wf.WalkForwardResult(
        draw_index=1, draw_date="03-01-2026", variant=list(range(7, 13)),
        hits=6, hits_union=6, wheel_coverage=100.0,
    )]
    summary = audit_cached_hits(flat, source, require_lottery("6/49", "RO"), 4, 4)
    assert summary[1] == {"pool": 6, "best_ticket": 6}
    pd.testing.assert_frame_equal(source, before)
    assert wf._csv_hash(source, "6/49") == source_hash
