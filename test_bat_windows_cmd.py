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

Nu putem rula cmd.exe în containerul Linux; testăm contractul care previne
regresia: .gitattributes + CRLF + echo fără paranteze + fără `echo.`.
"""
from __future__ import annotations

import re
from pathlib import Path

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
    # OneDrive + auto-gc after history rewrite: interactive
    # "Deletion of directory .git/objects/00 failed" hung START_8000.
    assert "gc.auto 0" in text or "gc.auto=0" in text
