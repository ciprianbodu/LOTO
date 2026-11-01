"""The completion receipt is tied to one bench run and is claimed once."""
import hashlib
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import psutil
import pytest
import app_nicegui as app
import bench_outcome

RUN = "a" * 32
PID = 726342


@pytest.fixture
def receipt_setup(tmp_path, monkeypatch):
    monkeypatch.setattr(bench_outcome, "RUNTIME_ROOT", tmp_path)
    monkeypatch.setattr(app, "BENCH_PID_FILE", tmp_path / ".bench_pid")
    monkeypatch.setattr(app, "_build_config_json", lambda: '{"datasets": []}')
    monkeypatch.setattr(app.ui, "notify", lambda *_a, **_kw: None)
    for key, value in {"bench_cancelled": False, "bench_was_running": False,
                       "_bench_process": None, "_bench_run_identity": None,
                       "_bench_seen_run": None, "_bench_survivors": []}.items():
        monkeypatch.setitem(app.STATE, key, value)
    monkeypatch.setitem(app.STATE, "_bench_waiting_sources", None)
    app._VIRTUAL_WF_CANCEL_REQUESTED.clear()
    calls = []
    monkeypatch.setattr(app, "_on_bench_finished", lambda: calls.append(True))
    config = hashlib.sha256(app._build_config_json().encode()).hexdigest()
    def pid_file(cancelled=False):
        suffix = "|cancelled" if cancelled else ""
        app.BENCH_PID_FILE.write_text(f"{PID}|1|{RUN}|{config}{suffix}", encoding="utf-8")
    def gone(_pid):
        raise psutil.NoSuchProcess(_pid)
    monkeypatch.setattr(psutil, "Process", gone)
    return pid_file, calls


def test_restart_success_is_confirmed_once(receipt_setup):
    pid_file, calls = receipt_setup
    pid_file()
    bench_outcome.record_exit(RUN, PID, 0)
    assert app._bench_running() is False
    app._observe_bench_activity(False)
    assert calls == [True]
    # Replay of the PID file cannot reuse a claimed receipt.
    pid_file()
    app._bench_running()
    app._observe_bench_activity(False)
    assert calls == [True]


@pytest.mark.parametrize("exit_code", [1, 2])
def test_failed_benchmark_does_not_run_history(receipt_setup, exit_code):
    pid_file, calls = receipt_setup
    pid_file()
    bench_outcome.record_exit(RUN, PID, exit_code)
    app._bench_running()
    app._observe_bench_activity(False)
    assert not calls


def test_killed_benchmark_has_no_receipt(receipt_setup):
    pid_file, calls = receipt_setup
    pid_file()
    app._bench_running()
    app._observe_bench_activity(False)
    assert not calls


def test_cancelled_marker_overrides_success(receipt_setup):
    pid_file, calls = receipt_setup
    pid_file(cancelled=True)
    bench_outcome.record_exit(RUN, PID, 0)
    app._bench_running()
    app._observe_bench_activity(False)
    assert not calls


def test_changed_data_after_restart_refuses_receipt(receipt_setup, monkeypatch):
    pid_file, calls = receipt_setup
    pid_file()
    bench_outcome.record_exit(RUN, PID, 0)
    monkeypatch.setattr(app, "_build_config_json", lambda: '{"datasets": ["changed"]}')
    app._bench_running()
    app._observe_bench_activity(False)
    assert not calls and app.STATE.get("_bench_waiting_sources")
    assert app.BENCH_PID_FILE.exists()
    monkeypatch.setattr(app, "_build_config_json", lambda: '{"datasets": []}')
    app._resume_bench_waiting_sources()
    app._resume_bench_waiting_sources()
    assert calls == [True]


def test_pid_recycled_or_wrong_receipt_cannot_fabricate_success(receipt_setup, monkeypatch):
    pid_file, calls = receipt_setup
    pid_file()
    # The old UUID receipt, even if the PID is reused, must name that PID.
    bench_outcome.record_exit(RUN, PID + 1, 0)
    proc = SimpleNamespace(status=lambda: "running", create_time=lambda: 1.0)
    monkeypatch.setattr(psutil, "Process", lambda _pid: proc)
    monkeypatch.setattr(app, "_bench_process_identity", lambda _proc: False)
    app._bench_running()
    app._observe_bench_activity(False)
    assert not calls


def test_claim_is_atomic_across_concurrent_consumers(receipt_setup):
    bench_outcome.record_exit(RUN, PID, 0)
    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = list(executor.map(lambda _: bench_outcome.claim_exit(RUN, PID), range(8)))
    assert outcomes.count(0) == 1
    assert outcomes.count(None) == 7


def test_malformed_receipts_and_tokens_are_rejected(receipt_setup):
    with pytest.raises(ValueError):
        bench_outcome.claim_exit("../stale", PID)
    marker = bench_outcome._path(RUN)
    marker.parent.mkdir(parents=True)
    marker.write_text('{"exit_code": 0, "pid": true}', encoding="utf-8")
    assert bench_outcome.claim_exit(RUN, PID) is None


def test_old_receipt_is_never_accepted(receipt_setup, monkeypatch):
    pid_file, calls = receipt_setup
    pid_file()
    bench_outcome.record_exit(RUN, PID, 0)
    original = bench_outcome.time.time()
    monkeypatch.setattr(bench_outcome.time, "time", lambda: original + 601)
    app._bench_running()
    app._observe_bench_activity(False)
    assert not calls


def test_no_timestamp_is_never_considered_fresh(receipt_setup):
    marker = bench_outcome._path(RUN)
    marker.parent.mkdir(parents=True)
    marker.write_text('{"run_id": "' + RUN + '", "pid": ' + str(PID) + ', "exit_code": 0}', encoding="utf-8")
    assert bench_outcome.peek_exit(RUN, PID) is None
