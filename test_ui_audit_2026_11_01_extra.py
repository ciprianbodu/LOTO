"""Regresii pentru raportarea walk-forward și varianta Joker."""
from dataclasses import replace
from types import SimpleNamespace

import pytest
import app_nicegui as app
import ui_hits
from scripts.analysis.audit_output import capture_ui
from test_history_official_prizes_ui import _entries
from test_ui_hits_section_2026_11_01 import _game_data, _patch_heavy_renderers


@pytest.mark.parametrize("meta, expected", [
    ({"large_pool_wf_skipped": True, "skip_reason": "variant_cap_required"}, "fără plafon"),
    ({"large_pool_wf_skipped": True, "skip_reason": "row_limit", "estimated_rows": 1600000, "row_limit": 1500000}, "1,600,000"),
    ({"wf_error": True}, "Walk-forward: eșuat"),
])
def test_empty_wf_reason_is_in_report_and_ui(monkeypatch, meta, expected):
    data = _game_data()
    monkeypatch.setitem(app.STATE, "results", ([("loto_6_49.csv", {"6/49": data})], 1))
    monkeypatch.setitem(app.STATE, "retro", {})
    monkeypatch.setitem(app.STATE, "retro_meta", {"loto_6_49.csv_6/49": meta})
    assert expected in app._build_report()
    _patch_heavy_renderers(monkeypatch)
    with capture_ui() as ui:
        ui_hits._render_game_analysis("loto_6_49.csv", "6/49", data)
    assert "Walk-forward" in ui.text()
    assert "Istoric hits" in ui.text()
    assert "eșuat" in ui.text() if meta.get("wf_error") else expected in ui.text()


@pytest.mark.parametrize("meta, expected", [
    ({"wf_error": True}, "Walk-forward eșuat"),
    ({"budget_skipped": True}, "bugetul de timp s-a terminat"),
    ({"decision_moved": True}, "decizia de bench s-a schimbat"),
    ({"scorer_fallback": True, "scorer": "frequency"}, "Walk-forward sărit"),
    ({"large_pool_wf_skipped": True, "skip_reason": "variant_cap_required"}, "fără plafon"),
])
def test_analysis_menu_preserves_wf_reason_without_folds(monkeypatch, tmp_path, meta, expected):
    monkeypatch.setattr(ui_hits, "PROJECT_ROOT", tmp_path)
    monkeypatch.setitem(app.STATE, "retro", {})
    monkeypatch.setitem(app.STATE, "retro_meta", {"loto_6_49.csv_6/49": meta})
    _patch_heavy_renderers(monkeypatch)
    with capture_ui() as ui:
        ui_hits._render_analysis_menu([("loto_6_49.csv", {"6/49": _game_data()})])
    assert "Analiză & Clasament" in ui.text()
    assert expected in ui.text()


def test_guarded_tree_does_not_kill_reused_pids(monkeypatch):
    import cleanup_old_processes as cleanup
    import psutil

    monkeypatch.setattr(cleanup, "_keep_pids", lambda: set())
    killed = []
    monkeypatch.setattr(cleanup, "_snapshot", lambda: [
        cleanup.ProcView(pid=10, create_time=200),
        cleanup.ProcView(pid=11, parent_pid=10, create_time=201),
    ])
    monkeypatch.setattr(cleanup, "_kill_pid", lambda *a, **k: killed.append(a[0]))
    assert cleanup.kill_pid_tree(10, expected_create_time=100) == []
    assert killed == []

    monkeypatch.undo()
    monkeypatch.setattr(cleanup, "_keep_pids", lambda: set())
    monkeypatch.setattr(cleanup, "_snapshot", lambda: [
        cleanup.ProcView(pid=10, create_time=100),
        cleanup.ProcView(pid=11, parent_pid=10, create_time=101),
        cleanup.ProcView(pid=12, parent_pid=10, create_time=None),
        cleanup.ProcView(pid=13, parent_pid=12, create_time=103),
    ])
    class Process:
        def __init__(self, pid): self.pid = pid
        def create_time(self): return {10: 100, 11: 201}[self.pid]
        def kill(self): killed.append(self.pid)
    monkeypatch.setattr(psutil, "Process", Process)
    assert cleanup.kill_pid_tree(10, expected_create_time=100) == [10]
    assert killed == [10]


def test_launcher_cleanup_does_not_kill_reused_or_unidentified_pids(monkeypatch, tmp_path):
    import cleanup_old_processes as cleanup
    import psutil

    venv = tmp_path / "venv"
    monkeypatch.setattr(cleanup, "_keep_pids", lambda: set())
    monkeypatch.setattr(cleanup, "_listen_pids", lambda _port: {12})
    monkeypatch.setattr(cleanup, "_snapshot", lambda: [
        cleanup.ProcView(pid=10, name="python.exe", exe=str(venv / "python.exe"), create_time=100),
        cleanup.ProcView(pid=11, parent_pid=10, name="python.exe", create_time=101),
        cleanup.ProcView(pid=12, name="python.exe", create_time=None),
    ])
    killed = []
    class Process:
        def __init__(self, pid): self.pid = pid
        def create_time(self): return {10: 200, 11: 101}[self.pid]
        def kill(self): killed.append(self.pid)
    monkeypatch.setattr(psutil, "Process", Process)
    assert cleanup.kill_stale_project_processes(
        venv_dir=venv, project_root=tmp_path, port=8000
    ) == [11]
    assert killed == [11]


@pytest.mark.parametrize("variant, flag, expected", [
    ([1, 2, 3, 4, 10, 7], True, "7 (nimerit)"),
    ([1, 2, 3, 4, 10, 8], False, "8 (nenimerit)"),
    ([1, 2, 3, 4, 10, 8], None, "8"),
    ([1, 2, 3, 4, 10], None, "—"),
])
def test_joker_table_displays_played_ball_separately(variant, flag, expected):
    row = _entries(game="joker")[0]
    row.variant = variant
    row.joker_hit = flag
    row.ticket_context["actual"] = [1, 2, 3, 4, 33]
    with capture_ui() as ui:
        ui_hits._render_hits_4plus([row], "joker", {"pool_size": 16})
    tables = [node for node in ui.walk() if node["kind"] == "table"]
    variants = [node for node in tables if "joker_variant" in {c["name"] for c in node["kwargs"]["columns"]}]
    assert variants
    assert all(t["kwargs"]["rows"][0]["joker_variant"] == expected for t in variants)
    assert variants[0]["kwargs"]["rows"][0]["ticket"] == "[1] [2] [3] [4] 10"


def test_partial_with_internal_gaps_never_claims_contiguous_recent_draws(monkeypatch):
    rows = _entries(n=3)
    rows = [replace(row, draw_index=index) for row, index in zip(rows, [1, 3, 5])]
    meta = {"pool_size": 16, "partial": True, "newest_missing": False, "n_test_draws": 3, "n_expected": 5}
    with capture_ui() as ui:
        ui_hits._render_hits_4plus(rows, "6/49", meta)
    assert "pot exista goluri între extragerile validate" in ui.text()
    assert "extragerile CELE MAI RECENTE" not in ui.text()
    monkeypatch.setitem(app.STATE, "results", ([("loto_6_49.csv", {"6/49": _game_data()})], 1))
    monkeypatch.setitem(app.STATE, "retro", {"loto_6_49.csv_6/49": rows})
    monkeypatch.setitem(app.STATE, "retro_meta", {"loto_6_49.csv_6/49": meta})
    assert "pot exista goluri între extragerile validate" in app._build_report()


def test_cancel_all_fallback_kills_only_matching_bench_in_project(monkeypatch, tmp_path):
    import psutil
    import cleanup_old_processes

    class Process:
        def __init__(self, pid, cmdline, cwd):
            self.pid = pid
            self.info = {"cmdline": cmdline}
            self._cwd = cwd
        def cwd(self):
            return str(self._cwd)
        def name(self):
            return self.info["cmdline"][0]
        def status(self):
            return "running"
        def cmdline(self):
            return self.info["cmdline"]
        def create_time(self):
            return 1000.0

    killed = []
    processes = [
        Process(10, ["python", "bench_all_methods.py"], tmp_path),
        Process(11, ["python", "bench_all_methods.py"], tmp_path / "other"),
        Process(12, ["python", "unrelated.py"], tmp_path),
        Process(13, ["notepad.exe", "bench_all_methods.py"], tmp_path),
        Process(14, ["python", "-m", "idlelib", "bench_all_methods.py"], tmp_path),
        Process(15, ["python", "-c", "pass", "bench_all_methods.py"], tmp_path),
        Process(16, ["python", "other.py", "bench_all_methods.py"], tmp_path),
    ]
    monkeypatch.setattr(psutil, "process_iter", lambda *_: processes)
    monkeypatch.setattr(cleanup_old_processes, "kill_pid_tree", lambda pid, **_: killed.append(pid))
    monkeypatch.setattr(app, "_bench_pid_status", lambda: (None, False))
    monkeypatch.setattr(app, "cancel_pending_running_jobs", lambda *_: None)
    monkeypatch.setattr(app, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(app, "BENCH_PID_FILE", tmp_path / "pid")
    monkeypatch.setattr(app.ui, "notify", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "_refresh_status", lambda: None)
    app.cancel_all()
    assert killed == [10]


@pytest.mark.parametrize("name,args,where,expected", [
    ("python.exe", ["python.exe", "bench_all_methods.py"], "root", True),
    ("pythonw.exe", ["pythonw.exe", "-uB", "bench_all_methods.py"], "root", True),
    ("python3.14", ["python3.14", "bench_all_methods.py"], "root", True),
    ("python.exe", ["python.exe", "bench_all_methods.py"], "other", False),
    ("notepad.exe", ["notepad.exe", "bench_all_methods.py"], "root", False),
    ("python.exe", ["python.exe", "-m", "idlelib", "bench_all_methods.py"], "root", False),
    ("python.exe", ["python.exe", "-Bm", "idlelib", "bench_all_methods.py"], "root", False),
    ("python.exe", ["python.exe", "-c", "pass", "bench_all_methods.py"], "root", False),
    ("python.exe", ["python.exe", "editor.py", "bench_all_methods.py"], "root", False),
    ("python.exe", ["python.exe", "bench_all_methods.py.old"], "root", False),
])
def test_bench_identity_is_required_for_pid_file_and_fallback(
    monkeypatch, tmp_path, name, args, where, expected
):
    import psutil

    class Process:
        def name(self): return name
        def cmdline(self): return args
        def cwd(self): return str(tmp_path if where == "root" else tmp_path / "other")
        def status(self): return "running"
        def create_time(self): return 1000.0

    proc = Process()
    monkeypatch.setattr(app, "PROJECT_ROOT", tmp_path)
    pidfile = tmp_path / ".bench_pid"
    pidfile.write_text("123|1000", encoding="utf-8")
    monkeypatch.setattr(app, "BENCH_PID_FILE", pidfile)
    monkeypatch.setattr(psutil, "Process", lambda _pid: proc)
    assert app._is_project_bench_process(proc) is expected
    assert app._verified_bench_pid() == (123 if expected else None)


def test_bench_absolute_script_exact_path_and_inaccessible_metadata(monkeypatch, tmp_path):
    import psutil

    class Process:
        def __init__(self, script): self.script = script
        def status(self): return "running"
        def name(self): return "python.exe"
        def cmdline(self): return ["python.exe", self.script]
        def cwd(self): raise psutil.AccessDenied()

    monkeypatch.setattr(app, "PROJECT_ROOT", tmp_path)
    assert app._is_project_bench_process(Process(str(tmp_path / "bench_all_methods.py")))
    assert not app._is_project_bench_process(Process("bench_all_methods.py"))
    assert not app._is_project_bench_process(Process(str(tmp_path / "other" / "bench_all_methods.py")))

    class Inaccessible(Process):
        def name(self): raise psutil.AccessDenied()
    assert not app._is_project_bench_process(Inaccessible(str(tmp_path / "bench_all_methods.py")))


@pytest.mark.parametrize("denied", ["name", "cmdline", "cwd", "create_time", "status"])
def test_inaccessible_bench_blocks_restart_but_never_authorizes_kill(monkeypatch, tmp_path, denied):
    import psutil
    import cleanup_old_processes

    class Process:
        pid = 123
        def get(self, name, value):
            if name == denied:
                raise psutil.AccessDenied()
            return value
        def status(self): return self.get("status", "running")
        def create_time(self): return self.get("create_time", 1000.0)
        def name(self): return self.get("name", "python.exe")
        def cmdline(self): return self.get("cmdline", ["python.exe", "bench_all_methods.py"])
        def cwd(self): return self.get("cwd", str(tmp_path))

    proc = Process()
    marker = tmp_path / ".bench_pid"
    marker.write_text("123|1000", encoding="utf-8")
    monkeypatch.setattr(app, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(app, "BENCH_PID_FILE", marker)
    monkeypatch.setattr(psutil, "Process", lambda _pid: proc)
    monkeypatch.setattr(psutil, "process_iter", lambda *_: [proc])
    monkeypatch.setattr(app, "cancel_pending_running_jobs", lambda *_: None)
    monkeypatch.setattr(app.ui, "notify", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "_refresh_status", lambda: None)
    killed, launched = [], []
    monkeypatch.setattr(cleanup_old_processes, "kill_pid_tree", lambda *a, **k: killed.append(a))
    monkeypatch.setattr(app.subprocess, "Popen", lambda *a, **k: launched.append(a))
    assert app._bench_running()
    app._launch_bench([], "test")
    assert not launched
    app.cancel_all()
    assert not killed
    assert marker.exists()
    assert app._bench_running()


def test_bench_marker_remains_if_kill_does_not_stop_process(monkeypatch, tmp_path):
    import psutil
    import cleanup_old_processes

    class Process:
        pid = 123
        def status(self): return "running"
        def create_time(self): return 1000.0
        def name(self): return "python.exe"
        def cmdline(self): return ["python.exe", "bench_all_methods.py"]
        def cwd(self): return str(tmp_path)

    proc = Process()
    marker = tmp_path / ".bench_pid"
    marker.write_text("123|1000", encoding="utf-8")
    monkeypatch.setattr(app, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(app, "BENCH_PID_FILE", marker)
    monkeypatch.setattr(psutil, "Process", lambda _pid: proc)
    monkeypatch.setattr(psutil, "process_iter", lambda *_: [])
    monkeypatch.setattr(cleanup_old_processes, "kill_pid_tree", lambda *a, **k: [])
    monkeypatch.setattr(app, "cancel_pending_running_jobs", lambda *_: None)
    monkeypatch.setattr(app.ui, "notify", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "_refresh_status", lambda: None)
    app.cancel_all()
    assert marker.exists()
    assert app._bench_running()


def test_bench_marker_is_removed_when_pid_becomes_another_process(monkeypatch, tmp_path):
    import psutil
    import cleanup_old_processes

    class Process:
        pid = 123
        current_name = "python.exe"
        def status(self): return "running"
        def create_time(self): return 1000.0
        def name(self): return self.current_name
        def cmdline(self): return [self.current_name, "bench_all_methods.py"]
        def cwd(self): return str(tmp_path)

    proc = Process()
    marker = tmp_path / ".bench_pid"
    marker.write_text("123|1000", encoding="utf-8")
    monkeypatch.setattr(app, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(app, "BENCH_PID_FILE", marker)
    monkeypatch.setattr(psutil, "Process", lambda _pid: proc)
    monkeypatch.setattr(psutil, "process_iter", lambda *_: [])
    def recycled(*_a, **_k):
        proc.current_name = "notepad.exe"
        return []
    monkeypatch.setattr(cleanup_old_processes, "kill_pid_tree", recycled)
    monkeypatch.setattr(app, "cancel_pending_running_jobs", lambda *_: None)
    monkeypatch.setattr(app.ui, "notify", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "_refresh_status", lambda: None)
    app.cancel_all()
    assert not marker.exists()
    assert not app._bench_running()


@pytest.mark.parametrize("write_fails", [False, True])
@pytest.mark.parametrize("kill_raises", [False, True])
def test_unmarked_surviving_benches_block_restart_even_if_marker_write_fails(
    monkeypatch, tmp_path, write_fails, kill_raises
):
    import psutil
    import cleanup_old_processes

    class Process:
        def __init__(self, pid): self.pid, self.alive = pid, True
        def status(self):
            if not self.alive:
                raise psutil.NoSuchProcess(self.pid)
            return "running"
        def create_time(self): return float(1000 + self.pid)
        def name(self): return "python.exe"
        def cmdline(self): return ["python.exe", "bench_all_methods.py"]
        def cwd(self): return str(tmp_path)

    procs = {10: Process(10), 11: Process(11)}
    marker = tmp_path / ".bench_pid"
    notifications = []
    monkeypatch.setitem(app.STATE, "_bench_survivors", [])
    monkeypatch.setattr(app, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(app, "BENCH_PID_FILE", marker)
    monkeypatch.setattr(psutil, "Process", lambda pid: procs[pid])
    monkeypatch.setattr(psutil, "process_iter", lambda *_: list(procs.values()))
    def kill(*_a, **_k):
        if kill_raises:
            raise OSError("access denied")
        return []
    monkeypatch.setattr(cleanup_old_processes, "kill_pid_tree", kill)
    monkeypatch.setattr(app, "cancel_pending_running_jobs", lambda *_: None)
    monkeypatch.setattr(app.ui, "notify", lambda msg, **_k: notifications.append(msg))
    monkeypatch.setattr(app, "_refresh_status", lambda: None)
    if write_fails:
        def fail(*_a, **_k): raise OSError("read only")
        monkeypatch.setattr(app, "atomic_write_text", fail)
    app.cancel_all()
    assert app._bench_running()
    assert marker.exists() is (not write_fails)
    assert any("nu a fost confirmată" in msg for msg in notifications)
    # Markerul poate privi numai primul PID; al doilea continuă să blocheze.
    procs[10].alive = False
    assert app._bench_running()
    procs[11].alive = False
    assert not app._bench_running()


def test_cancelled_bench_finishing_never_dispatches_autopilot_but_new_bench_does(
    monkeypatch, tmp_path
):
    import psutil

    monkeypatch.setitem(app.STATE, "bench_cancelled", False)
    monkeypatch.setitem(app.STATE, "bench_was_running", True)
    monkeypatch.setitem(app.STATE, "_bench_survivors", [])
    monkeypatch.setattr(app, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(app, "BENCH_PID_FILE", tmp_path / ".bench_pid")
    monkeypatch.setattr(psutil, "process_iter", lambda *_: [])
    monkeypatch.setattr(app, "cancel_pending_running_jobs", lambda *_: None)
    monkeypatch.setattr(app.ui, "notify", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "_refresh_status", lambda: None)
    finished = []
    monkeypatch.setattr(app, "_on_bench_finished", lambda: finished.append(True))
    app.cancel_all()
    assert app.STATE["bench_cancelled"] is True
    assert "Auto-Pilot nu va porni" in app._bench_status_text()
    # Un benchmark pe care nu l-am putut opri este văzut încă viu, apoi termină.
    app._observe_bench_activity(True)
    app._observe_bench_activity(False)
    assert not finished
    assert app.STATE["bench_cancelled"] is True

    # Numai o nouă pornire explicită ridică anularea și permite finalizarea.
    monkeypatch.setattr(app, "_bench_running", lambda: False)
    monkeypatch.setattr(app.subprocess, "Popen", lambda *_a, **_k: SimpleNamespace(pid=123, poll=lambda: 0))
    app._launch_bench([], "Re-Bench")
    assert app.STATE["bench_cancelled"] is False
    assert "Auto-Pilot pornește" in app._bench_status_text()
    assert len((tmp_path / ".bench_pid").read_text(encoding="utf-8").split("|")) == 4
    import bench_outcome
    monkeypatch.setattr(bench_outcome, "peek_exit", lambda *_: 0)
    monkeypatch.setattr(bench_outcome, "claim_exit", lambda *_: 0)
    monkeypatch.setattr(bench_outcome, "mark_processed", lambda *_: None)
    app._observe_bench_activity(True)
    app._observe_bench_activity(False)
    assert finished == [True]


def test_failed_explicit_bench_launch_does_not_clear_cancellation(monkeypatch, tmp_path):
    monkeypatch.setitem(app.STATE, "bench_cancelled", True)
    monkeypatch.setattr(app, "_bench_running", lambda: False)
    monkeypatch.setattr(app, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(app.ui, "notify", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "_refresh_status", lambda: None)
    def failed(*_a, **_k): raise OSError("cannot start")
    monkeypatch.setattr(app.subprocess, "Popen", failed)
    app._launch_bench([], "Re-Bench")
    assert app.STATE["bench_cancelled"] is True


def test_cancelled_bench_marker_blocks_autopilot_after_ui_restart(monkeypatch, tmp_path):
    import psutil
    import cleanup_old_processes

    class Process:
        pid = 123
        alive = True
        def status(self):
            if not self.alive: raise psutil.NoSuchProcess(self.pid)
            return "running"
        def create_time(self): return 1000.0
        def name(self): return "python.exe"
        def cmdline(self): return ["python.exe", "bench_all_methods.py"]
        def cwd(self): return str(tmp_path)

    proc = Process()
    marker = tmp_path / ".bench_pid"
    marker.write_text("123|1000", encoding="utf-8")
    monkeypatch.setitem(app.STATE, "bench_cancelled", False)
    monkeypatch.setitem(app.STATE, "bench_was_running", True)
    monkeypatch.setitem(app.STATE, "_bench_survivors", [])
    monkeypatch.setattr(app, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(app, "BENCH_PID_FILE", marker)
    monkeypatch.setattr(psutil, "Process", lambda _pid: proc)
    monkeypatch.setattr(psutil, "process_iter", lambda *_: [])
    monkeypatch.setattr(cleanup_old_processes, "kill_pid_tree", lambda *a, **k: [])
    monkeypatch.setattr(app, "cancel_pending_running_jobs", lambda *_: None)
    monkeypatch.setattr(app.ui, "notify", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "_refresh_status", lambda: None)
    app.cancel_all()
    assert marker.read_text(encoding="utf-8") == "123|1000|cancelled"

    # Simulăm un proces UI nou, fără marcajele din memorie.
    app.STATE["bench_cancelled"] = False
    app.STATE["bench_was_running"] = False
    app.STATE["_bench_survivors"] = []
    finished = []
    monkeypatch.setattr(app, "_on_bench_finished", lambda: finished.append(True))
    app._observe_bench_activity(app._bench_running())
    assert app.STATE["bench_cancelled"] is True
    proc.alive = False
    app._observe_bench_activity(app._bench_running())
    assert not finished
    assert not marker.exists()


def test_unreadable_bench_marker_does_not_interrupt_other_cancellations(monkeypatch, tmp_path):
    import psutil

    marker = tmp_path / ".bench_pid"
    marker.write_bytes(b"\xff\xfe")
    monkeypatch.setattr(app, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(app, "BENCH_PID_FILE", marker)
    monkeypatch.setitem(app.STATE, "_bench_survivors", [])
    monkeypatch.setattr(psutil, "process_iter", lambda *_: [])
    calls, notifications = [], []
    monkeypatch.setattr(app, "cancel_pending_running_jobs", lambda *_: calls.append("jobs"))
    monkeypatch.setattr(app.ui, "notify", lambda msg, **_kw: notifications.append(msg))
    monkeypatch.setattr(app, "_refresh_status", lambda: calls.append("refresh"))
    assert app._bench_cancel_marker() is False
    app.cancel_all()
    assert calls == ["jobs", "refresh"]
    assert any("nu a fost confirmată" in msg for msg in notifications)
