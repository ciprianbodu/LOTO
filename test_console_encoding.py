"""Regression: scripturile pornite din .bat pe Windows CMD nu crapa la diacritice."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

_ROMANIAN_PY_MSG = (
    "Python 3.14.7 OK (ACTUALIZARI.bat menține ultimul patch stabil 3.14.x)"
)


def test_cp1252_strict_fails_on_romanian_without_reconfigure():
    buf = io.BytesIO()
    stream = io.TextIOWrapper(buf, encoding="cp1252", errors="strict")
    with pytest.raises(UnicodeEncodeError):
        stream.write(_ROMANIAN_PY_MSG)


def test_utf8_reconfigure_allows_romanian_on_cp1252_backed_stream():
    buf = io.BytesIO()
    stream = io.TextIOWrapper(buf, encoding="cp1252", errors="strict")
    stream.reconfigure(encoding="utf-8", errors="replace")

    stream.write(f"Python: 3.14.7 — {_ROMANIAN_PY_MSG}\n")
    stream.flush()
    out = buf.getvalue().decode("utf-8")
    assert "Python:" in out
    assert "men" in out


def test_startup_scripts_reconfigure_console_streams():
    for name in ("verify_imports.py", "verifica_mediu.py", "update_csv.py", "reset_jobs.py"):
        text = Path(name).read_text(encoding="utf-8")
        assert 'reconfigure(encoding="utf-8"' in text, f"{name} lipseste reconfigure UTF-8"
