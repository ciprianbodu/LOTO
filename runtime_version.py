"""Versiunea Python acceptată de punctele de intrare LOTO."""

from __future__ import annotations

import sys

PYTHON_SERIES = (3, 15)


def check_python_version() -> tuple[bool, str]:
    """Acceptă orice patch stabil din seria Python țintă."""
    version = sys.version_info
    detected = sys.version.split()[0]
    if version[:2] != PYTHON_SERIES or version.releaselevel != "final":
        major, minor = PYTHON_SERIES
        return False, (
            f"Python {detected} detectat. LOTO cere Python {major}.{minor}.x stabil. "
            "Rulează ACTUALIZARI.bat pentru actualizarea mediului."
        )
    return True, f"Python {detected} OK"


def require_python_version() -> None:
    ok, message = check_python_version()
    if not ok:
        raise RuntimeError(message)
