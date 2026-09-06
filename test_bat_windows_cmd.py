"""Gărzi pentru lansatoarele .bat — CMD.EXE pe Windows.

Simptomele reale din START_8000.bat (2026-08-25):
  'f' is not recognized as an internal or external command
  'ho.' is not recognized as an internal or external command
  toate ramurile de sync git tipărite odată, (main) dispărut din echo

Cauze:
  1. .bat cu LF în loc de CRLF — `for /f` e citit ca respectiva comandă `/f`
     (Windows raportează 'f'), iar `echo.` ca 'ho.'.
  2. Paranteze rotunde în `echo` DINĂUNTRUL unui bloc `if (` / `for (` —
     chiar și `^(main^)`: caret-ul e consumat la parse-ul blocului, apoi
     `(main)` închide if-ul. Urmare: stash/reset --hard necondiționat.
  3. Un launcher face `CALL` la helper, iar helperul rulează `git reset` care
     înlocuiește launcherul încă activ. CMD revine la același offset de byte în
     fișierul NOU și execută un fragment de linie (`----------------...`).

Nu putem rula cmd.exe în containerul Linux; testăm contractul care previne
regresia: .gitattributes + CRLF + echo fără paranteze + fără `echo.`.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent

# `>> "file" echo.` sau `echo.` de unul singur — nu `echo something.`
_ECHO_DOT = re.compile(
    r"^(?:>{1,2}\s*(?:\"[^\"]+\"|\S+)\s+)?echo\.\s*$",
    re.IGNORECASE,
)
# Comandă echo (nu @echo off/on). Captură restul liniei.
_ECHO_CMD = re.compile(
    r"^(?:@{0,1}(?:>{1,2}\s*(?:\"[^\"]+\"|\S+)\s+)?)echo(?P<body>\s+.*)$",
    re.IGNORECASE,
)


def _bat_files() -> list[Path]:
    return sorted(ROOT.glob("*.bat"))


def test_gitattributes_forces_crlf_on_bat():
    ga = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert re.search(r"(?m)^\s*\*\.bat\s+.*eol=crlf", ga), (
        ".gitattributes trebuie să forțeze CRLF pe *.bat — altfel git checkout "
        "pe Windows lasă LF și CMD rupe for /f + echo."
    )


def test_bat_files_exist_and_use_crlf():
    bats = _bat_files()
    assert bats, "nu am găsit niciun .bat în rădăcină"
    names = {p.name for p in bats}
    assert {"START_8000.bat", "loto_git_sync.bat", "ACTUALIZARI.bat"} <= names
    for p in bats:
        data = p.read_bytes()
        assert b"\r\n" in data, f"{p.name} nu are CRLF"
        leftover = data.replace(b"\r\n", b"")
        assert b"\n" not in leftover, (
            f"{p.name} are newline LF fără CR — CMD.EXE pe Windows va raporta "
            f"'f' / 'ho.' is not recognized"
        )


def test_bat_echo_has_no_dot_blank_and_no_parentheses():
    """echo. e fragil pe LF; ( ) în echo închide blocurile if din CMD."""
    offenders_dot: list[str] = []
    offenders_paren: list[str] = []
    for p in _bat_files():
        text = p.read_text(encoding="utf-8")
        for i, raw in enumerate(text.splitlines(), 1):
            line = raw.strip()
            if not line or line.upper().startswith("REM"):
                continue
            if _ECHO_DOT.match(line):
                offenders_dot.append(f"{p.name}:{i}: {line}")
                continue
            m = _ECHO_CMD.match(line)
            if not m:
                continue
            body = m.group("body")
            # @echo off / echo on — nu sunt mesaje
            if body.strip().lower() in {"off", "on"}:
                continue
            if "(" in body or ")" in body:
                offenders_paren.append(f"{p.name}:{i}: {line}")
    assert not offenders_dot, (
        "folosește echo/ în loc de echo. (CMD + LF → 'ho.' is not recognized):\n"
        + "\n".join(offenders_dot)
    )
    assert not offenders_paren, (
        "scoate parantezele rotunde din echo — în bloc if ( ) CMD le tratează "
        "ca delimitatori de bloc, chiar și cu ^ :\n"
        + "\n".join(offenders_paren)
    )


def test_loto_git_sync_avoids_delayed_expansion_and_paren_echo():
    """main 03f5409: fara delayed expansion; echo/REM fara paranteze.

    Varianta cu goto :au_need_reset de pe ramura PR e echivalenta ca intentie,
    dar delayed expansion + echo cu paranteze a fost cauza 'Auto-update is not
    recognized'. Pastram contractul de pe main.
    """
    text = (ROOT / "loto_git_sync.bat").read_text(encoding="utf-8")
    assert "EnableDelayedExpansion" not in text
    assert "git reset --hard origin/main" in text
    assert "^(main^)" not in text
    assert "(main)" not in text
    assert "(backup in stash" not in text


def test_updates_installs_latest_python314_without_hardcoded_patch():
    """Fallback-ul fără winget detectează online patch-ul; nu îmbătrânește în cod."""
    text = (ROOT / "ACTUALIZARI.bat").read_text(encoding="utf-8")
    compact = text.replace(" ", "").lower()

    assert "call :ensure_latest_python314" in text
    assert text.index("call :ensure_latest_python314") < text.index(
        'if not exist "%VENV_PY%"'
    ), "Python trebuie instalat înainte de prima creare a venv-ului"
    assert "winget upgrade -e --id Python.Python.3.14 --source winget" in text
    assert "winget install -e --id Python.Python.3.14 --source winget" in text
    assert "https://www.python.org/ftp/python/" in text
    assert r"3\.14\.\d+/" in text
    assert "python-!py_latest!-amd64.exe" in compact
    assert "python-3.14.6" not in text
    assert "choice /C YN" not in text, "migrarea venv-ului trebuie să fie automată"


def test_python314_detection_validates_the_real_interpreter():
    """Textul de eroare al launcherului nu poate deveni o versiune fictivă."""
    updater = (ROOT / "ACTUALIZARI.bat").read_text(encoding="utf-8")
    detector = (ROOT / "scripts" / "find_python314.ps1").read_text(encoding="utf-8")

    assert "call :detect_python314" in updater
    assert "py -3.14 --version 2^>^&1" not in updater
    assert "py -3.14 -m venv" not in updater
    assert '"%PY314_EXE%" -m venv' in updater
    assert "sys.version_info[:2] == (3, 14)" in detector
    assert "sys.executable + '|' + sys.version.split()[0]" in detector
    assert "Python314\\python.exe" in detector


def test_updates_preserves_archival_requirements_snapshot_and_checks_installer():
    text = (ROOT / "ACTUALIZARI.bat").read_text(encoding="utf-8")
    assert "requirements_before_upgrade.txt" in text
    assert "set REQ_SNAPSHOT=requirements_snapshot.txt" not in text
    assert "Get-AuthenticodeSignature" in text
    assert "Python Software Foundation" in text
    assert "PY_INSTALL_RC" in text


def test_updates_cannot_treat_two_missing_versions_as_success():
    text = (ROOT / "ACTUALIZARI.bat").read_text(encoding="utf-8")
    start = text.index('if "!PY_LATEST!"=="" (', text.index("winget install"))
    end = text.index('if "!SYS_VER!"=="!PY_LATEST!"', start)
    no_online = text[start:end]
    assert "goto :ep_download_latest" in no_online


def test_start_requires_prepared_venv_instead_of_creating_an_empty_one():
    text = (ROOT / "START_8000.bat").read_text(encoding="utf-8")
    verify = text[text.index("\n:verify_phase"):text.index("\n:launch_phase")]
    assert "-m venv" not in verify
    assert "ACTUALIZARI.bat" in verify


def test_startup_log_uses_external_runtime_dir():
    text = (ROOT / "START_8000.bat").read_text(encoding="utf-8")
    assert 'set "RUNTIME_DIR=%LOTO_RUNTIME_DIR%"' in text
    assert 'set "RUNTIME_DIR=D:\\_BUILD\\_LOTO"' in text
    assert 'set "LOGFILE=%RUNTIME_DIR%\\startup_8000.log"' in text
    assert 'set "LOGFILE=%PROJECT_DIR%startup_8000.log"' not in text


def test_start_stops_when_queue_reset_fails():
    text = (ROOT / "START_8000.bat").read_text(encoding="utf-8")
    launch = text[text.index("\n:launch_phase"):text.index("\n:push_istoric")]
    reset_pos = launch.index('reset_jobs.py" --force')
    failure_guard = launch.index("if errorlevel 1", reset_pos)
    worker_pos = launch.index('start "LOTO WORKER"')
    assert reset_pos < failure_guard < worker_pos
    assert "exit /b 30" in launch[failure_guard:worker_pos]


def test_environment_check_uses_canonical_history_directory():
    text = (ROOT / "verifica_mediu.py").read_text(encoding="utf-8")
    assert 'Path("_ISTORIC")' in text
    assert 'Path("ISTORIC")' not in text


def test_updates_integrity_check_matches_cpu_requirements():
    """Updater-ul nu mai verifică dependențe eliminate precum numba/streamlit."""
    text = (ROOT / "ACTUALIZARI.bat").read_text(encoding="utf-8")
    active = "\n".join(
        line for line in text.splitlines()
        if line.strip() and not line.lstrip().upper().startswith("REM")
    ).lower()
    assert "import numpy,pandas,scipy,sklearn,statsmodels,nicegui" in active
    assert "import numpy,pandas,scipy,numba" not in active
    assert "taskkill /f /t /im streamlit.exe" not in active


def test_updates_migrates_wf_cache_out_of_onedrive():
    text = (ROOT / "ACTUALIZARI.bat").read_text(encoding="utf-8")
    assert "migrate_legacy_wf_cache" in text
    assert "purge_stale_wf_cache(dry_run=False)" in text
    assert text.index("migrate_legacy_wf_cache") < text.index("purge_stale_wf_cache(dry_run=False)")


def test_python_version_check_has_no_stale_patch_constant():
    text = (ROOT / "ui_shared.py").read_text(encoding="utf-8")
    assert "PYTHON_TARGET_PATCH" not in text
    assert "ultimul patch stabil 3.14.x" in text


@pytest.mark.parametrize("launcher", ["ACTUALIZARI.bat", "START_8000.bat"])
def test_self_updating_launchers_sync_from_immutable_temp_copy(launcher):
    """Niciun context .bat din repo nu rămâne pe stack în timpul git reset."""
    text = (ROOT / launcher).read_text(encoding="utf-8")
    bootstrap_label = text.index("\n:bootstrap_sync\n")
    post_label = text.index("\n:post_sync\n")
    main_label = text.index("\n:main\n")
    normal = text[:bootstrap_label]
    bootstrap = text[bootstrap_label:post_label]
    post = text[post_label:main_label]

    assert f'copy /Y "%~f0" "%BOOT_DIR%\\{launcher}" >nul || goto :bootstrap_failed' in normal
    assert (
        'copy /Y "%~dp0loto_git_sync.bat" "%BOOT_DIR%\\loto_git_sync.bat" '
        '>nul || goto :bootstrap_failed'
    ) in normal
    transfer = f'"%BOOT_DIR%\\{launcher}" --bootstrap-sync'
    assert transfer in normal
    assert f"call {transfer}".lower() not in normal.lower()

    assert 'call "%BOOT_DIR%\\loto_git_sync.bat" autoupdate "%PROJECT_DIR%"' in bootstrap
    resume = f'"%PROJECT_DIR%{launcher}" --post-sync'
    assert resume in bootstrap
    assert f"call {resume}".lower() not in bootstrap.lower()
    assert 'rmdir /s /q "%BOOT_DIR%"' in post


def test_forced_sync_preserves_ahead_commit_and_tracked_changes():
    """Stash-ul singur nu salvează commit-ul local `ahead`; trebuie branch backup."""
    text = (ROOT / "loto_git_sync.bat").read_text(encoding="utf-8")
    force = text[text.index(":force_sync"):text.index(":push_istoric")]

    assert 'backup/auto-sync-' in force
    assert 'git branch "%_BACKUP_BRANCH%" HEAD' in force
    assert 'git stash push -m "auto-backup before forced sync"' in force
    assert force.index('git branch "%_BACKUP_BRANCH%" HEAD') < force.index(
        "git reset --hard origin/main"
    )
    assert force.index('git stash push -m "auto-backup before forced sync"') < force.index(
        "git reset --hard origin/main"
    )


def test_temp_git_helper_accepts_explicit_repository_root():
    text = (ROOT / "loto_git_sync.bat").read_text(encoding="utf-8")
    assert 'set "_ROOT=%~2"' in text
    assert 'cd /d "%_ROOT%"' in text


def test_start8000_kills_old_processes_without_project_path_cmdline_filter():
    """Bench-ul UI e pornit relativ; filtrul `CommandLine like %~dp0` nu-l vedea.

    Copiii ProcessPool trebuie omorâți explicit (taskkill /T / Python tree-kill):
    pe Windows uciderea părintelui NU omoară descendenții.
    """
    text = (ROOT / "START_8000.bat").read_text(encoding="utf-8")
    launch = text[text.index("\n:launch_phase"):text.index("\n:push_istoric")]
    assert "cleanup_old_processes.py" in launch
    assert "--venv" in launch
    assert "CommandLine -like '*%~dp0*'" not in launch
    assert "Stop-Process" not in launch
    compact = " ".join(launch.lower().split())
    assert "taskkill /f /t /pid" in compact
    assert 'findstr /c:":8000 "' in compact
