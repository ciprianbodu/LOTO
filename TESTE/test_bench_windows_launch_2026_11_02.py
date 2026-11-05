"""Real Python subprocesses must confirm the PID tracked by the UI."""
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

import app_nicegui as app
import bench_outcome
import rebench_history_store
from TESTE.test_rebench_selected_sources import session


@pytest.mark.parametrize("resumed", [False, True])
@pytest.mark.parametrize("exit_code", [0, 1])
def test_real_python_entry_confirms_ui_launcher(session, monkeypatch, tmp_path, resumed, exit_code):
    """Keep the production entry block and real OS PIDs; stub only bench work."""
    monkeypatch.setattr(bench_outcome, "RUNTIME_ROOT", tmp_path)
    monkeypatch.setattr(rebench_history_store, "STORE_FILE", tmp_path / "history.sqlite3")
    monkeypatch.setenv("LOTO_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(app, "BENCH_PID_FILE", tmp_path / ".bench_pid")
    monkeypatch.setattr(app, "_refresh_status", lambda: None)
    for key in ("_bench_process", "_bench_run_identity", "_bench_seen_run", "_bench_context_required",
                "bench_was_running", "virtual_wf_status", "virtual_wf_dirty", "analysis_expanded"):
        monkeypatch.setitem(app.STATE, key, None)
    driver = tmp_path / "bench_entry.py"
    driver.write_text(
        "import ast, pathlib, sys\n"
        f"root = pathlib.Path({str(Path(__file__).resolve().parents[1])!r})\n"
        "sys.path.insert(0, str(root))\n"
        "import bench_all_methods as bench\n"
        f"bench.main = lambda: {exit_code}\n"
        "tree = ast.parse((root / 'bench_all_methods.py').read_text(encoding='utf-8'))\n"
        "entry = next(n for n in reversed(tree.body) if isinstance(n, ast.If) and isinstance(n.test, ast.Compare))\n"
        "exec(compile(ast.Module(body=entry.body, type_ignores=[]), 'bench_all_methods.py', 'exec'), vars(bench))\n",
        encoding="utf-8",
    )
    real_popen = subprocess.Popen
    launched = []

    def launch(command, **kwargs):
        assert command[1] == "bench_all_methods.py"
        kwargs["creationflags"] = 0  # No visible test console on Windows.
        proc = real_popen([command[0], str(driver)], **kwargs)
        launched.append(proc)
        return proc

    monkeypatch.setattr(app.subprocess, "Popen", launch)
    observed = []
    monkeypatch.setattr(app, "_on_bench_finished", lambda **kw: observed.append(kw))
    proc = None
    try:
        app.run_rebench()
        proc, = launched
        assert proc.wait(timeout=40) == exit_code
        pid, run_id, _ = app.STATE["_bench_run_identity"]
        receipt = json.loads(bench_outcome._path(run_id).read_text(encoding="utf-8"))
        assert pid == proc.pid == receipt["pid"]
        assert type(receipt["runtime_pid"]) is int
        if resumed:
            monkeypatch.setitem(app.STATE, "_bench_process", None)
        if exit_code:
            monkeypatch.setitem(app.STATE, "virtual_wf_status", "Analiză istorică încheiată.")
            monkeypatch.setitem(app.STATE, "virtual_wf_dirty", True)
        app._observe_bench_activity(False)
        app._observe_bench_activity(False)
        if exit_code == 0:
            assert len(observed) == 1
            config = json.loads(app._bench_history_config(observed[0]["context"]))
            assert {t["game_label"]: t["pool_size"] for d in config["datasets"] for t in d["tasks"]} == {
                "6/49": 16, "joker": 12, "5/40": 19,
            }
        else:
            assert observed == []
            assert "istoricul automat nu a pornit" in app.STATE["virtual_wf_status"]
            assert app.STATE["virtual_wf_dirty"] and app.STATE["analysis_expanded"]
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=10)


def test_pending_launcher_does_not_consume_success_receipt(monkeypatch, tmp_path):
    run_id, pid = "f" * 32, 123456
    monkeypatch.setattr(bench_outcome, "RUNTIME_ROOT", tmp_path)
    monkeypatch.setattr(app, "BENCH_PID_FILE", tmp_path / ".bench_pid")
    bench_outcome.record_exit(run_id, pid, 0)
    state = {"bench_was_running": True, "bench_cancelled": False,
             "_bench_process": SimpleNamespace(poll=lambda: None),
             "_bench_run_identity": (pid, run_id, "0" * 64)}
    for key, value in state.items():
        monkeypatch.setitem(app.STATE, key, value)
    calls = []
    monkeypatch.setattr(app, "_on_bench_finished", lambda **kw: calls.append(kw))
    app._observe_bench_activity(False)
    assert app.STATE["bench_was_running"] is True
    assert bench_outcome.peek_exit(run_id, pid) == 0
    assert not bench_outcome.is_claimed(run_id)
    assert calls == []


def test_recycled_parent_does_not_change_receipt_pid(monkeypatch):
    import psutil

    monkeypatch.setenv(bench_outcome.UI_PID_ENV, "10")
    processes = {30: SimpleNamespace(pid=30, ppid=lambda: 20, create_time=lambda: 100),
                 20: SimpleNamespace(pid=20, ppid=lambda: 10, create_time=lambda: 200)}
    monkeypatch.setattr(psutil, "Process", lambda pid: processes[pid])
    assert bench_outcome._launcher_pid(30) == 30
