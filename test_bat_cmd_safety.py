"""Safety checks for Windows .bat launchers (cmd.exe parse traps).

cmd.exe + EnableDelayedExpansion + ``echo ^(text^)`` can leave an unmatched
``(`` so the next REM/echo word runs as a command
(``'Auto-update' is not recognized``). UTF-8 em-dash (U+2014) is 0x94 on
CP1252, which is a quote character and can break ``if (`` blocks.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent
BATS = ("ACTUALIZARI.bat", "START_8000.bat", "loto_git_sync.bat")
EM_DASH_UTF8 = "\u2014".encode("utf-8")
CARET_OPEN_PAREN = b"^("


def _read(name: str) -> bytes:
    return (ROOT / name).read_bytes()


def test_bat_files_exist_and_ascii():
    for name in BATS:
        raw = _read(name)
        raw.decode("ascii")
        assert EM_DASH_UTF8 not in raw, f"{name} contains UTF-8 em-dash"


def test_no_caret_escaped_parens_in_bats():
    """Ban caret-escaped '(' even outside delayed expansion: it is a footgun
    if someone later adds setlocal EnableDelayedExpansion."""
    for name in BATS:
        raw = _read(name)
        assert CARET_OPEN_PAREN not in raw, f"{name} contains caret-escaped '('"


def test_actualizari_delayed_expansion_only_in_clean_ghosts():
    text = _read("ACTUALIZARI.bat").decode("ascii")
    setlocals: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if s.lower().startswith("rem"):
            continue
        if s.lower().startswith("setlocal"):
            setlocals.append(s)
    assert setlocals, "ACTUALIZARI.bat must call setlocal"
    first = setlocals[0].lower().replace(" ", "")
    assert "enabledelayedexpansion" not in first, (
        "file-scope setlocal must not enable delayed expansion "
        "(call loto_git_sync.bat inherits it)"
    )
    rest = setlocals[1:]
    assert rest, "CleanGhosts must enable delayed expansion locally"
    assert all("enabledelayedexpansion" in s.lower().replace(" ", "") for s in rest)
    assert ":CleanGhosts" in text
    ghosts = text.split(":CleanGhosts", 1)[1]
    assert "EnableDelayedExpansion" in ghosts
    assert "endlocal" in ghosts.split("exit /b", 1)[0]


def test_actualizari_keeps_git_and_env_steps():
    text = _read("ACTUALIZARI.bat").decode("ascii")
    needles = [
        'call "%~dp0loto_git_sync.bat" autoupdate',
        'call "%~dp0loto_git_sync.bat" push_istoric',
        "requirements_base.txt",
        "update_csv.py",
        "purge_stale_wf_cache",
        "verifica_mediu.py",
        ":CleanGhosts",
        ":ensure_python314",
        ":check_integrity",
        "py -3.14",
        r"D:\_BUILD\_LOTO\.venv",
        "Python.Python.3.14",
    ]
    missing = [n for n in needles if n not in text]
    assert not missing, f"ACTUALIZARI.bat lost steps: {missing}"


def test_git_sync_disables_inherited_delayed_expansion():
    text = _read("loto_git_sync.bat").decode("ascii")
    assert "DisableDelayedExpansion" in text
    assert "git push origin main" in text
    assert 'if /I "%~1"=="autoupdate" goto autoupdate' in text
    assert 'if /I "%~1"=="push_istoric" goto push_istoric' in text
