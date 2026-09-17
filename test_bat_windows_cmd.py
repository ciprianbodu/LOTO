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


def test_gitattributes_forces_lf_on_git_hooks():
    ga = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert re.search(r"(?m)^\s*scripts/git-hooks/\*\s+.*eol=lf", ga)


def test_post_commit_hook_auto_pushes_main_without_force():
    hook = ROOT / "scripts" / "git-hooks" / "post-commit"
    data = hook.read_bytes()
    assert data.startswith(b"#!/bin/sh")
    assert b"\r" not in data, "hook-ul trebuie LF — Git Bash pe Windows"
    text = data.decode("utf-8")
    assert "LOTO_SKIP_AUTO_PUSH" in text
    assert "git push --quiet origin main" in text
    assert "--force" not in text
    assert "GIT_TERMINAL_PROMPT=0" in text
    assert "rebase-merge" in text


def test_launchers_install_versioned_hooks_path():
    for name in ("START_8000.bat", "ACTUALIZARI.bat"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "core.hooksPath" in text, name
        assert "scripts/git-hooks" in text, name



def test_bat_files_exist_and_use_crlf():
    bats = _bat_files()
    assert bats, "nu am găsit niciun .bat în rădăcină"
    names = {p.name for p in bats}
    assert {"START_8000.bat", "ACTUALIZARI.bat"} <= names
    assert "loto_git_sync.bat" not in names
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
        "ca delimitatori de bloc, chiar și cu ^ :\n" + "\n".join(offenders_paren)
    )


def test_bat_rem_has_no_parentheses_inside_a_block():
    """Aceeasi capcana ca la echo (linia de mai sus), dar pentru REM: un `(`/`)`
    dintr-un comentariu care sta ÎN INTERIORUL unui bloc `if (`/`for (` inca
    deschis inchide blocul la fel de "orb" ca un echo cu paranteze — REM nu e
    tratat special de parserul cmd.exe cand vine vorba de paranteze nebalansate.

    Testul de mai sus interzice paranteze in echo PESTE TOT (blanket), dar sare
    peste REM complet — desi genereaza EXACT acelasi risc. O interdictie blanket
    si pe REM ar cere rescrierea a ~28 de comentarii top-level deja sigure (in
    afara oricarui bloc) din aceste fisiere; in loc de asta, verificam adancimea
    reala de bloc: un `(`/`)` intr-un REM conteaza DOAR cand REM-ul e deja
    in interiorul unui bloc deschis de un `if (`/`for (` anterior si inca
    neinchis. Regresie: exact bug-ul asta a fost scris (si prins la recitire,
    inainte de commit) in timpul verificarii globale din 2026-09-07, de doua ori,
    in doua fisiere diferite — REM-uri explicative adaugate langa un fix real,
    cu paranteze, in interiorul unui `if errorlevel 1 (`."""
    depth = 0
    offenders: list[str] = []
    for p in _bat_files():
        text = p.read_text(encoding="utf-8")
        depth = 0
        for i, raw in enumerate(text.splitlines(), 1):
            line = raw.strip()
            if not line:
                continue
            if line.upper().startswith("REM"):
                if depth > 0 and ("(" in line or ")" in line):
                    offenders.append(f"{p.name}:{i} (adancime {depth}): {line}")
                continue
            # Linie de cod (nu REM): actualizeaza adancimea din delta net de
            # paranteze — o pereche echilibrata pe acelasi rand (ex. un
            # subshell `('cmd')` intr-un `for /f`) se anuleaza reciproc, deci
            # doar dezechilibrul net (ex. `if X (` sau `) else (` sau `)` simplu)
            # schimba adancimea, exact cum le interpreteaza cmd.exe.
            delta = line.count("(") - line.count(")")
            depth = max(0, depth + delta)
    assert not offenders, (
        "scoate parantezele din REM cat timp e INTERIOR unui bloc if(/for( deschis "
        "— cmd.exe nu trateaza REM diferit de orice alt text cand numara parantezele "
        "unui bloc nebalansat:\n" + "\n".join(offenders)
    )


_SELF_CLOSED_QUOTED_LINE = re.compile(r'^"[^"]*"\s*\^?\s*$')


def test_no_caret_escaped_pipe_inside_self_closed_quoted_segments():
    """Un rand care e ÎN ÎNTREGIME un string dublu-cotat autonom (se deschide și
    se închide pe același rând, eventual urmat de ^ de continuare pentru rândul
    următor) NU tratează caret-ul ca escape — un "^|"/"^<"/"^>"/"^&" acolo ajunge
    LITERAL la programul apelat (ex. PowerShell -Command), nu devine "|"/"<"/">"/"&".

    Regresie (verificare globală 2026-09-07): ACTUALIZARI.bat detecta ultimul
    patch Python 3.14 online printr-un -Command PowerShell scris ca 5 segmente
    dublu-cotate autonome, îmbinate cu ^ la capăt de linie — unul din ele avea
    "^|" în loc de "|", ceea ce strica silențios (>nul 2>&1) parsarea PowerShell
    de fiecare dată, dezactivând permanent fallback-ul fără winget."""
    offenders = []
    for p in _bat_files():
        text = p.read_text(encoding="utf-8")
        for i, raw in enumerate(text.splitlines(), 1):
            line = raw.strip()
            if _SELF_CLOSED_QUOTED_LINE.match(line) and re.search(r"\^[|<>&]", line):
                offenders.append(f"{p.name}:{i}: {line}")
    assert not offenders, (
        "caret inutil (si daunator) inaintea unui operator, DINTR-UN string "
        "dublu-cotat autonom — caret-ul nu e escape in interiorul ghilimelelor:\n"
        + "\n".join(offenders)
    )


_ENDLOCAL_EXIT_LINE = re.compile(r"endlocal\s*&\s*exit\s*/b\s+(\S+)", re.IGNORECASE)


def test_endlocal_exit_never_uses_delayed_expansion_variable():
    """`endlocal & exit /b !VAR!` pe un singur rând: `endlocal` oprește delayed
    expansion ÎNAINTE ca `exit` să ruleze pe același rând (comenzile legate prin
    & rulează secvențial, dar !VAR! se rezolvă la EXECUȚIE, nu la parse) — deci
    !VAR! s-ar rezolva gol/literal, pierzând codul de retur real. Trebuie %VAR%
    (rezolvat o dată, la PARSE-ul întregului rând, înainte ca endlocal să ruleze).

    Regresie: START_8000.bat propaga astfel gresit RC-ul real al
    verify_imports.py — cel mai sigur pas de verificare din pornire putea lasa
    scriptul sa continue si sa lanseze aplicatia peste un mediu stricat."""
    offenders = []
    for p in _bat_files():
        text = p.read_text(encoding="utf-8")
        for i, raw in enumerate(text.splitlines(), 1):
            m = _ENDLOCAL_EXIT_LINE.search(raw)
            if m and m.group(1).startswith("!"):
                offenders.append(f"{p.name}:{i}: {raw.strip()}")
    assert not offenders, (
        "endlocal & exit /b !VAR! pierde valoarea reala — foloseste %VAR%:\n"
        + "\n".join(offenders)
    )


def test_no_external_git_helper_bat():
    """Codul se publica pe GitHub din mediul de audit; lansatoarele nu mai apeleaza un .bat helper."""
    assert not (ROOT / "loto_git_sync.bat").exists()
    for name in ("START_8000.bat", "ACTUALIZARI.bat"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "loto_git_sync" not in text, name
        assert "--bootstrap-sync" not in text, name
        assert "git reset --hard" not in text, name


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
    verify = text[text.index("\n:verify_phase") : text.index("\n:launch_phase")]
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
    launch = text[text.index("\n:launch_phase") : text.index("\n:push_istoric")]
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
        line
        for line in text.splitlines()
        if line.strip() and not line.lstrip().upper().startswith("REM")
    ).lower()
    assert "import numpy,pandas,scipy,sklearn,statsmodels,nicegui" in active
    assert "import numpy,pandas,scipy,numba" not in active
    assert "taskkill /f /t /im streamlit.exe" not in active


def test_updates_migrates_wf_cache_out_of_onedrive():
    text = (ROOT / "ACTUALIZARI.bat").read_text(encoding="utf-8")
    assert "migrate_legacy_wf_cache" in text
    assert "purge_stale_wf_cache(dry_run=False)" in text
    assert text.index("migrate_legacy_wf_cache") < text.index(
        "purge_stale_wf_cache(dry_run=False)"
    )


def test_python_version_check_has_no_stale_patch_constant():
    text = (ROOT / "ui_shared.py").read_text(encoding="utf-8")
    assert "PYTHON_TARGET_PATCH" not in text
    assert "ultimul patch stabil 3.14.x" in text


def test_launchers_relaunch_via_runtime_dir_updater():
    """Nu tragem .bat-ul aflat in rulare. Scriem updater pe D:\\_BUILD\\_LOTO, iesim, pull, repornim."""
    for name in ("START_8000.bat", "ACTUALIZARI.bat"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert ":bootstrap_sync" not in text, name
        assert "--bootstrap-sync" not in text, name
        assert "loto_relaunch.bat" in text, name
        assert 'start "LOTO UPDATE"' in text, name
        assert "LOTO_RELAUNCHED" in text, name
        assert "git pull --ff-only origin main" in text, name
        assert "git checkout origin/main -- START_8000.bat ACTUALIZARI.bat" in text, name
        assert "git reset --hard" not in text, name
        assert "loto_git_sync" not in text, name
    start = (ROOT / "START_8000.bat").read_text(encoding="utf-8")
    actual = (ROOT / "ACTUALIZARI.bat").read_text(encoding="utf-8")
    assert "git pull --ff-only origin main <nul" not in start
    assert "git pull --ff-only origin main <nul" not in actual
    assert 'set "RELAUNCH=START_8000.bat"' in start
    assert 'set "RELAUNCH=ACTUALIZARI.bat"' in actual



def test_start8000_kills_old_processes_without_project_path_cmdline_filter():
    """Bench-ul UI e pornit relativ; filtrul `CommandLine like %~dp0` nu-l vedea.

    Copiii ProcessPool trebuie omorâți explicit (taskkill /T / Python tree-kill):
    pe Windows uciderea părintelui NU omoară descendenții.
    """
    text = (ROOT / "START_8000.bat").read_text(encoding="utf-8")
    launch = text[text.index("\n:launch_phase") : text.index("\n:push_istoric")]
    assert "cleanup_old_processes.py" in launch
    assert "--venv" in launch
    assert "CommandLine -like '*%~dp0*'" not in launch
    assert "Stop-Process" not in launch
    compact = " ".join(launch.lower().split())
    assert "taskkill /f /t /pid" in compact
    assert 'findstr /c:":8000 "' in compact


def test_push_istoric_uses_git_exe_not_helper_bat():
    """Calea cu spatii nu mai trece prin CALL la un .bat helper."""
    for name in ("START_8000.bat", "ACTUALIZARI.bat"):
        text = (ROOT / name).read_text(encoding="utf-8")
        body = text[text.index("\n:push_istoric\n") :]
        nxt = body.find("\n:", 2)
        if nxt != -1:
            body = body[:nxt]
        assert "loto_git_sync" not in body, name
        assert "git push origin main" in body, name
        assert "git add -A -- _ISTORIC" in body, name


def test_start8000_opens_ipv4_loopback_not_localhost():
    """Chrome pe Windows rezolvă localhost ca ::1; NiceGUI ascultă IPv4."""
    text = (ROOT / "START_8000.bat").read_text(encoding="utf-8")
    launch = text[text.index("\n:launch_phase") : text.index("\n:push_istoric")]
    assert "http://127.0.0.1:8000" in launch
    assert "start http://localhost:8000" not in launch
    assert "timeout /t 12" in launch


def test_launchers_delete_stray_root_bats():
    """Pe disc pot rămâne loto_git_sync.bat sau copii vechi; le ștergem."""
    for name in ("START_8000.bat", "ACTUALIZARI.bat"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert '%~dp0*.bat' in text, name
        assert 'if /I not "%%~nxF"=="START_8000.bat"' in text, name
        assert 'if /I not "%%~nxF"=="ACTUALIZARI.bat"' in text, name
        assert "del /f /q" in text, name
        assert "loto_git_sync" not in text, name
