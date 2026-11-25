"""Startup cleanup can act only on documented LOTO processes."""

from pathlib import Path

import cleanup_old_processes as cleanup


def test_restart_does_not_stop_another_service_and_its_children(monkeypatch, tmp_path):
    venv = tmp_path / "loto-venv"
    procs = [
        cleanup.ProcView(
            10, name="python.exe", exe=str(venv / "Scripts" / "python.exe"),
            cmdline=("python.exe", "worker.py"), cwd=str(tmp_path),
            create_time=100.0,
        ),
        cleanup.ProcView(20, name="node.exe", create_time=101.0),
        cleanup.ProcView(21, name="node.exe", parent_pid=20, create_time=102.0),
    ]
    killed = []
    monkeypatch.setattr(cleanup, "_snapshot", lambda: procs)
    monkeypatch.setattr(cleanup, "_keep_pids", lambda: set())
    monkeypatch.setattr(cleanup, "_listen_pids", lambda port: {20})
    monkeypatch.setattr(
        cleanup, "_kill_pid",
        lambda pid, *, expected_create_time: killed.append((pid, expected_create_time)) or True,
    )

    cleanup.kill_stale_project_processes(venv_dir=venv, project_root=tmp_path)

    assert killed == [(10, 100.0)]


def test_batch_never_tree_kills_the_port_owner_and_checks_before_reset():
    text = (Path(__file__).resolve().parents[1] / "START_8000.bat").read_text("utf-8")
    launch = text.split("\n:launch_phase\n", 1)[1].split("\n:push_istoric\n", 1)[0]
    before_reset = launch.split("reset_jobs.py", 1)[0]
    assert "taskkill" not in launch.lower()
    after_cleanup = before_reset.split("cleanup_old_processes.py", 1)[1]
    assert "if errorlevel 1" in after_cleanup.lower()
    assert "exit /b" in after_cleanup.lower()


def test_environment_upgrade_only_matches_its_exact_environment():
    import os
    import re
    import shutil
    import subprocess

    import pytest

    powershell = shutil.which("powershell")
    if os.name != "nt" or powershell is None:
        pytest.skip("The batch script uses Windows PowerShell")

    text = (Path(__file__).resolve().parents[1] / "ACTUALIZARI.bat").read_text("utf-8")
    filters = re.findall(r"Where-Object \{([^{}\r\n]*ExecutablePath[^{}\r\n]*)\}", text)
    assert len(filters) == 3
    for expression in filters:
        predicate = expression.replace(r'\"', '"')
        script = (
            "$venv = 'D:\\_BUILD\\_LOTO\\.venv'\n"
            "$items = @(\n"
            "  [pscustomobject]@{ ProcessId = 10; ExecutablePath = "
            "'d:\\_build\\_loto\\.venv\\Scripts\\python.exe' }\n"
            "  [pscustomobject]@{ ProcessId = 20; ExecutablePath = "
            "'D:\\_BUILD\\_LOTO\\.venv_backup\\Scripts\\python.exe' }\n"
            "  [pscustomobject]@{ ProcessId = 30; ExecutablePath = $null }\n"
            ")\n"
            f"$items | Where-Object {{{predicate}}} | ForEach-Object ProcessId"
        )
        proc = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=10, check=True,
        )
        assert proc.stdout.strip().splitlines() == ["10"]


def test_cleanup_cli_waits_for_loto_to_close_before_relaunch(monkeypatch, tmp_path):
    venv = tmp_path / "venv"
    worker = cleanup.ProcView(
        10, name="python.exe", exe=str(venv / "Scripts" / "python.exe"),
        cmdline=("python.exe", "worker.py"), cwd=str(tmp_path), create_time=100.0,
    )
    pending = [worker]
    calls = []

    def stop(pid, *, expected_create_time):
        assert (pid, expected_create_time) == (10, 100.0)
        pending.clear()
        calls.append("stop")
        return True

    def address_available(port):
        assert port == 8000 and not pending
        calls.append("probe")
        return True

    monkeypatch.setattr(cleanup, "_snapshot", lambda: list(pending))
    monkeypatch.setattr(cleanup, "_listen_pids", lambda port: set())
    monkeypatch.setattr(cleanup, "_keep_pids", lambda: set())
    monkeypatch.setattr(cleanup, "_kill_pid", stop)
    monkeypatch.setattr(cleanup, "_local_port_available", address_available)
    assert cleanup.main(["--venv", str(venv), "--project-root", str(tmp_path)]) == 0
    assert calls == ["stop", "probe"]


def test_failed_worker_stop_blocks_relaunch(monkeypatch, tmp_path, capsys):
    venv = tmp_path / "venv"
    worker = cleanup.ProcView(
        10, name="python.exe", exe=str(venv / "Scripts" / "python.exe"), create_time=100.0,
    )
    monkeypatch.setattr(cleanup, "_snapshot", lambda: [worker])
    monkeypatch.setattr(cleanup, "_keep_pids", lambda: set())
    monkeypatch.setattr(cleanup, "_local_port_available", lambda port: True)

    assert not cleanup._wait_for_startup_clear(str(venv), str(tmp_path), 8000, timeout=0)
    assert "10" in capsys.readouterr().out


def test_local_probe_distinguishes_free_and_bound_socket():
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as holder:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            holder.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        holder.bind(("127.0.0.1", 0))
        _, port = holder.getsockname()
        assert not cleanup._local_port_available(port)
    assert cleanup._local_port_available(port)


def test_environment_check_refuses_global_python_before_pip(monkeypatch):
    import pytest
    import verifica_mediu

    monkeypatch.setattr(verifica_mediu, "check_python_version", lambda: (True, "OK"))
    monkeypatch.setattr(verifica_mediu, "_is_in_venv", lambda: False)
    monkeypatch.setattr(verifica_mediu, "check_scoring_stack", lambda: None)
    monkeypatch.setattr(verifica_mediu, "check_bench_assets", lambda: True)
    monkeypatch.setattr(verifica_mediu, "upgrade_pip", lambda: pytest.fail("global pip upgrade"))
    monkeypatch.setattr(verifica_mediu, "check_and_upgrade", lambda packages: pytest.fail("global package upgrade"))
    with pytest.raises(SystemExit) as exc:
        verifica_mediu.main()
    assert exc.value.code != 0


def test_residual_collection_keeps_tracked_alias_to_untracked_file(monkeypatch, tmp_path):
    import cleanup_residual

    project = tmp_path / "project"
    project.mkdir()
    tracked_path = project / ".machine_profile"
    tracked_path.touch()
    target = project / "untracked.txt"
    target.touch()
    process_resolve = Path.resolve

    def resolve(path, *args, **kwargs):
        if path == tracked_path:
            return target
        return process_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    result = cleanup_residual.collect_candidates(project, project, {".machine_profile"})
    assert tracked_path not in {path for path, _ in result}


def test_reset_refuses_to_guess_a_database_if_the_queue_module_failed(monkeypatch):
    import builtins
    import runpy
    from types import SimpleNamespace

    project = Path(__file__).resolve().parents[1]
    real_import = builtins.__import__

    def import_without_queue(name, *args, **kwargs):
        if name == "job_queue":
            raise ImportError("simulated queue module failure")
        return real_import(name, *args, **kwargs)

    def unexpected_sql(*args, **kwargs):
        raise AssertionError("SQLite must not open a guessed database")

    monkeypatch.setattr(builtins, "__import__", import_without_queue)
    script = runpy.run_path(str(project / "reset_jobs.py"))
    main = script["main"]
    monkeypatch.setitem(main.__globals__, "os", SimpleNamespace(path=SimpleNamespace(exists=lambda path: True)))
    monkeypatch.setitem(main.__globals__, "sqlite3", SimpleNamespace(connect=unexpected_sql, Error=Exception))
    assert main() != 0


def test_launcher_cleanup_does_not_follow_a_snapshot_junction():
    import os
    import shutil
    import subprocess

    import pytest

    powershell = shutil.which("powershell")
    if os.name != "nt" or powershell is None:
        pytest.skip("The launcher uses Windows PowerShell")

    launcher = Path(__file__).resolve().parents[1] / "scripts" / "launcher_git.ps1"
    quoted_launcher = str(launcher).replace("'", "''")
    script = (
        "$dir = Join-Path ([IO.Path]::GetTempPath()) 'loto-launch-12-34'\n"
        "function Get-Item { param($LiteralPath, [switch]$Force, $ErrorAction)\n"
        "  [pscustomobject]@{ PSIsContainer = $true;"
        " Attributes = [IO.FileAttributes]::Directory -bor [IO.FileAttributes]::ReparsePoint }\n"
        "}\n"
        "function Test-Path { param($LiteralPath, $PathType) return $true }\n"
        "function Get-ChildItem { param($LiteralPath, [switch]$Force) return @() }\n"
        "function Remove-Item { param($LiteralPath, [switch]$Force) Write-Output 'deletion requested' }\n"
        f"& '{quoted_launcher}' -Mode Cleanup -ProjectDir '.' -SnapshotDir $dir"
    )
    proc = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode == 0, proc.stderr
    assert "deletion requested" not in proc.stdout


def test_startup_requires_the_executed_script_to_belong_to_loto(tmp_path):
    project = tmp_path / "loto"
    other = tmp_path / "other"
    venv = tmp_path / "venv"
    local_worker = str(project / "worker.py")
    other_worker = str(other / "worker.py")
    python = str(other / "python.exe")
    commands = [
        ((python, other_worker), False),
        ((python, "../other/worker.py"), False),
        ((python, str(other / "service.py"), local_worker), False),
        ((python, "-m", "some_service", local_worker), False),
        ((python, "-X", "utf8", "-u", local_worker), True),
        ((python, "-u", "worker.py"), True),
    ]
    for cmd, owned in commands:
        proc = cleanup.ProcView(10, "python.exe", python, cmd, str(project))
        assert cleanup.is_stale_proc(
            proc, venv_dir=venv, project_root=project, keep_pids=set()
        ) is owned, cmd


def test_versioned_interpreter_requires_exact_posix_script_path():
    root = "/var/local/loto"
    exe = "/opt/python/bin/python3.15"
    cases = [
        ("python3.15", "/var/local/loto/worker.py", True),
        ("python3.15", "/var/local/unrelated/worker.py", False),
        ("python3.15", "worker.py", True),
        ("python3.15-helper", "/var/local/loto/worker.py", False),
    ]
    for name, script, expected in cases:
        process = cleanup.ProcView(23, name, exe, (exe, "-X", "utf8", script), root)
        assert cleanup.is_stale_proc(
            process, venv_dir="/var/local/loto-env", project_root=root, keep_pids=set(),
        ) is expected
