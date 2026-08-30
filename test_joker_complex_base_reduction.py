"""Smoke pentru testul complex de reducere a bazei pe Joker."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCRIPT = ROOT / "scripts" / "analysis" / "joker_complex_base_reduction.py"


def test_joker_complex_base_reduction_smoke():
    assert SCRIPT.is_file()
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--smoke"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "smoke ok" in r.stdout
