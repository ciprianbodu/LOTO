"""Teste pentru analiza_patterns_last10.py — K se detecta din coloanele
completate, nu din geometria reala; loto_5_40.csv (6 coloane) rula gresit
ca "6/40". Fix: geometria se recunoaste din numele fisierului."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pandas as pd

SCRIPT = Path(__file__).resolve().parent / "analiza_patterns_last10.py"


def _run(csv_path: Path) -> str:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(csv_path), "20"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def _draw_size(out: str) -> int:
    """K din linia `Joc: K/MAXN` — MAXN depinde de datele sintetice, K nu."""
    m = re.search(r"Joc: (\d+)/\d+", out)
    assert m, out
    return int(m.group(1))


def _write_5_40_csv(path: Path, n_rows: int = 60) -> None:
    """6 coloane populate (n1..n6), geometrie reala 5/40 -- exact forma
    _ISTORIC/loto_5_40.csv."""
    rows = []
    for i in range(n_rows):
        base = (i % 35) + 1
        rows.append(
            {
                "date": f"01-01-{2000 + i}",
                "n1": base,
                "n2": base + 1,
                "n3": base + 2,
                "n4": base + 3,
                "n5": base + 4,
                "n6": base + 5,
            }
        )
    pd.DataFrame(rows).to_csv(path, index=False)


def test_5_40_filename_forces_five_columns_despite_six_populated(tmp_path):
    csv = tmp_path / "loto_5_40.csv"
    _write_5_40_csv(csv)
    assert _draw_size(_run(csv)) == 5


def test_6_49_filename_still_uses_six_columns(tmp_path):
    csv = tmp_path / "loto_6_49.csv"
    _write_5_40_csv(csv)  # aceleasi date brute, doar numele fisierului difera
    assert _draw_size(_run(csv)) == 6


def test_unknown_filename_falls_back_to_populated_column_detection(tmp_path):
    csv = tmp_path / "mystery_game.csv"
    _write_5_40_csv(csv)
    assert _draw_size(_run(csv)) == 6
