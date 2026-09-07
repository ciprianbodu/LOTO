"""Cleanup-ul de la START_8000 trebuie să omoare bench-ul relativ + copiii ProcessPool."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from cleanup_old_processes import (
    ProcView,
    cmdline_script_names,
    is_stale_proc,
    kill_pid_tree,
    kill_stale_project_processes,
    path_is_under,
    select_stale_pids,
)

VENV = r"D:\_BUILD\_LOTO\.venv"
ROOT = r"D:\_LIBRARIES\OneDrive\_CODING\_LOTO"
KEEP = {111, 0}


def _py(
    pid: int,
    cmdline: list[str],
    *,
    exe: str | None = None,
    cwd: str | None = None,
    ppid: int | None = None,
    name: str = "python.exe",
) -> ProcView:
    return ProcView(
        pid=pid,
        name=name,
        exe=exe,
        cmdline=tuple(cmdline),
        cwd=cwd,
        parent_pid=ppid,
    )


def test_path_is_under_accepts_windows_separators_on_linux():
    assert path_is_under(
        r"D:\_BUILD\_LOTO\.venv\Scripts\python.exe",
        r"D:\_BUILD\_LOTO\.venv",
    )
    assert not path_is_under(
        r"C:\Python314\python.exe",
        r"D:\_BUILD\_LOTO\.venv",
    )


def test_cmdline_script_names_uses_basename():
    names = cmdline_script_names([
        r"D:\_BUILD\_LOTO\.venv\Scripts\python.exe",
        r"D:\_LIBRARIES\OneDrive\_CODING\_LOTO\worker.py",
    ])
    assert "worker.py" in names
    assert "python.exe" in names


def test_bench_relative_cmdline_without_project_path_is_stale():
    """Regresia din START_8000: filtrul `CommandLine like %~dp0` rata bench-ul UI."""
    proc = _py(
        20,
        [r"D:\_BUILD\_LOTO\.venv\Scripts\python.exe", "bench_all_methods.py"],
        exe=r"D:\_BUILD\_LOTO\.venv\Scripts\python.exe",
        cwd=ROOT,
    )
    assert is_stale_proc(proc, venv_dir=VENV, project_root=ROOT, keep_pids=KEEP)


def test_process_pool_child_without_script_name_is_stale_via_venv():
    proc = _py(
        21,
        [
            r"D:\_BUILD\_LOTO\.venv\Scripts\python.exe",
            "-c",
            "from multiprocessing.spawn import spawn_main; spawn_main()",
        ],
        exe=r"D:\_BUILD\_LOTO\.venv\Scripts\python.exe",
        cwd=ROOT,
        ppid=20,
    )
    assert is_stale_proc(proc, venv_dir=VENV, project_root=ROOT, keep_pids=KEEP)


def test_old_filter_would_miss_bench_and_pool_child():
    """Documentează de ce filtrul vechi (script AND %~dp0 pe CommandLine) e greșit."""
    bench_cmd = " ".join([
        r"D:\_BUILD\_LOTO\.venv\Scripts\python.exe",
        "bench_all_methods.py",
    ])
    pool_cmd = " ".join([
        r"D:\_BUILD\_LOTO\.venv\Scripts\python.exe",
        "-c",
        "from multiprocessing.spawn import spawn_main; spawn_main()",
    ])
    assert ROOT.lower() not in bench_cmd.lower()
    assert ROOT.lower() not in pool_cmd.lower()
    assert "bench_all_methods.py" in bench_cmd
    assert "bench_all_methods.py" not in pool_cmd
    assert "worker.py" not in pool_cmd
    assert "app_nicegui.py" not in pool_cmd


def test_other_projects_worker_is_not_stale():
    proc = _py(
        30,
        [r"C:\Python314\python.exe", r"C:\other\worker.py"],
        exe=r"C:\Python314\python.exe",
        cwd=r"C:\other",
    )
    assert not is_stale_proc(proc, venv_dir=VENV, project_root=ROOT, keep_pids=KEEP)


def test_our_worker_with_absolute_path_is_stale_even_outside_venv():
    proc = _py(
        31,
        [r"C:\Python314\python.exe", rf"{ROOT}\worker.py"],
        exe=r"C:\Python314\python.exe",
        cwd=ROOT,
    )
    assert is_stale_proc(proc, venv_dir=VENV, project_root=ROOT, keep_pids=KEEP)


def test_self_pid_is_never_stale():
    proc = _py(
        111,
        [rf"{VENV}\Scripts\python.exe", "cleanup_old_processes.py"],
        exe=rf"{VENV}\Scripts\python.exe",
        cwd=ROOT,
    )
    assert not is_stale_proc(proc, venv_dir=VENV, project_root=ROOT, keep_pids=KEEP)


def test_ui_and_worker_from_start_bat_are_stale():
    ui = _py(
        40,
        [rf"{VENV}\Scripts\python.exe", rf"{ROOT}\app_nicegui.py"],
        exe=rf"{VENV}\Scripts\python.exe",
        cwd=ROOT,
    )
    worker = _py(
        41,
        [rf"{VENV}\Scripts\python.exe", rf"{ROOT}\worker.py"],
        exe=rf"{VENV}\Scripts\python.exe",
        cwd=ROOT,
    )
    assert is_stale_proc(ui, venv_dir=VENV, project_root=ROOT, keep_pids=KEEP)
    assert is_stale_proc(worker, venv_dir=VENV, project_root=ROOT, keep_pids=KEEP)


def test_select_includes_descendants_and_port_listener():
    bench = _py(
        20,
        [rf"{VENV}\Scripts\python.exe", "bench_all_methods.py"],
        exe=rf"{VENV}\Scripts\python.exe",
        cwd=ROOT,
    )
    child = _py(
        21,
        [rf"{VENV}\Scripts\python.exe", "-c", "spawn_main()"],
        exe=rf"{VENV}\Scripts\python.exe",
        cwd=ROOT,
        ppid=20,
    )
    grandchild = _py(
        22,
        [rf"{VENV}\Scripts\python.exe", "-c", "resource_tracker()"],
        exe=rf"{VENV}\Scripts\python.exe",
        cwd=ROOT,
        ppid=21,
    )
    # Copil NON-python: expand_descendants trebuie să-l includă (echivalent taskkill /T)
    conhost = ProcView(
        pid=23,
        name="conhost.exe",
        exe=r"C:\Windows\System32\conhost.exe",
        parent_pid=20,
    )
    other = ProcView(pid=80, name="node.exe", exe=r"C:\nodejs\node.exe", parent_pid=1)
    stale = select_stale_pids(
        [bench, child, grandchild, conhost, other],
        venv_dir=VENV,
        project_root=ROOT,
        keep_pids=KEEP,
        listen_pids={80},
    )
    assert stale == {20, 21, 22, 23, 80}


def test_port_8000_does_not_match_80001():
    """findstr :8000 fără spațiu ar fi potrivit :80001; matcher-ul e pe port exact."""
    listener = ProcView(pid=90, name="python.exe", exe=r"C:\Python314\python.exe")
    stale = select_stale_pids(
        [listener],
        venv_dir=VENV,
        project_root=ROOT,
        keep_pids=KEEP,
        listen_pids=set(),
    )
    assert stale == set()


def test_non_python_not_matched_unless_listening():
    notepad = ProcView(
        pid=50,
        name="notepad.exe",
        exe=r"C:\Windows\notepad.exe",
        cmdline=(r"C:\Windows\notepad.exe",),
        cwd=ROOT,
    )
    assert not is_stale_proc(notepad, venv_dir=VENV, project_root=ROOT, keep_pids=KEEP)


def test_start8000_calls_python_cleanup_and_tree_kills_port():
    text = Path("START_8000.bat").read_text(encoding="utf-8")
    launch = text[text.index("\n:launch_phase"):text.index("\n:push_istoric")]
    assert "cleanup_old_processes.py" in launch
    assert "--venv" in launch
    assert "CommandLine -like '*%~dp0*'" not in launch
    assert "Stop-Process" not in launch
    compact = " ".join(launch.lower().split())
    assert "taskkill /f /t /pid" in compact
    assert 'findstr /c:":8000 "' in compact


def test_kills_spawned_worker_without_touching_this_pytest(tmp_path):
    """E2E: un worker.py din alt folder e omorât; pytest rămâne în viață."""
    worker = tmp_path / "worker.py"
    worker.write_text("import time; time.sleep(60)\n", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, str(worker)],
        cwd=str(tmp_path),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    killed: list[int] = []
    try:
        deadline = time.time() + 5
        while time.time() < deadline:
            if proc.poll() is not None and proc.pid not in killed:
                break
            killed = kill_stale_project_processes(
                venv_dir=tmp_path / "no-venv",
                project_root=tmp_path,
                port=0,
            )
            if proc.pid in killed:
                break
            time.sleep(0.05)
        assert proc.pid in killed
        proc.wait(timeout=5)
        assert proc.poll() is not None
        assert Path(__file__).exists()  # pytest n-a fost omorât
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_kill_pid_tree_kills_the_whole_tree(tmp_path):
    """cancel_all() are un singur PID cunoscut (bench_all_methods.py); tree-kill-ul
    trebuie să ajungă și la copiii lui ProcessPoolExecutor, nu doar la părinte."""
    child_script = tmp_path / "child.py"
    child_script.write_text("import time; time.sleep(60)\n", encoding="utf-8")
    parent_script = tmp_path / "parent.py"
    parent_script.write_text(
        "import subprocess, sys, time\n"
        f"c = subprocess.Popen([sys.executable, {str(child_script)!r}])\n"
        "print(c.pid, flush=True)\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    parent = subprocess.Popen(
        [sys.executable, str(parent_script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        child_pid_line = parent.stdout.readline().strip()
        assert child_pid_line, "copilul nu și-a raportat PID-ul"
        child_pid = int(child_pid_line)
        import psutil
        assert psutil.pid_exists(child_pid)

        acted = kill_pid_tree(parent.pid)

        assert parent.pid in acted
        assert child_pid in acted
        parent.wait(timeout=5)
        deadline = time.time() + 5
        while psutil.pid_exists(child_pid) and time.time() < deadline:
            time.sleep(0.05)
        assert not psutil.pid_exists(child_pid), "copilul a rămas orfan în viață"
        assert Path(__file__).exists()  # pytest n-a fost omorât
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=5)
        try:
            if psutil.pid_exists(child_pid):
                psutil.Process(child_pid).kill()
        except Exception:  # noqa: BLE001
            pass


def test_kill_pid_tree_on_already_dead_pid_is_a_noop():
    """PID mort (deja terminat) nu trebuie să crape apelantul — cancel_all()
    poate ajunge aici după ce bench-ul s-a terminat singur între timp."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(timeout=5)
    dead_pid = proc.pid
    assert kill_pid_tree(dead_pid) == []


def test_kill_pid_tree_dry_run_reports_without_killing(tmp_path):
    script = tmp_path / "sleeper.py"
    script.write_text("import time; time.sleep(60)\n", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, str(script)])
    try:
        acted = kill_pid_tree(proc.pid, dry_run=True)
        assert proc.pid in acted
        assert proc.poll() is None  # tot in viata: dry-run n-a omorat nimic
    finally:
        proc.kill()
        proc.wait(timeout=5)
