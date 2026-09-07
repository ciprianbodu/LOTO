"""Teste pentru audit_patterns_and_designs.py — validarea extragerilor era
reimplementata manual, in loc sa foloseasca `draw_validation.valid_draw_matrix`
(CLAUDE.md §4.1); ramane fail-loud, doar definitia de "valid" s-a aliniat."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

import scripts.analysis.audit_patterns_and_designs as apd


def _write_istoric(root: Path, rows: list[dict]) -> None:
    (root / "_ISTORIC").mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(root / "_ISTORIC" / "loto_6_49.csv", index=False)


def test_audit_game_rejects_decimal_value_via_canonical_validation(tmp_path, monkeypatch):
    """to_numpy(dtype=int) trunchia 5.7 -> 5 tacut; valid_draw_matrix il respinge."""
    rows = [
        {"date": "01-01-2020", "n1": 1, "n2": 2, "n3": 3, "n4": 4, "n5": 5, "n6": 6},
        {"date": "02-01-2020", "n1": 1, "n2": 2, "n3": 3, "n4": 4, "n5": 5.7, "n6": 6},
    ]
    _write_istoric(tmp_path, rows)
    monkeypatch.setattr(apd, "ROOT", tmp_path)

    with pytest.raises(ValueError, match="numere invalide"):
        apd.audit_game("6/49", 11)


def test_audit_game_rejects_duplicate_number_in_a_draw(tmp_path, monkeypatch):
    rows = [
        {"date": "01-01-2020", "n1": 1, "n2": 2, "n3": 3, "n4": 4, "n5": 5, "n6": 6},
        {"date": "02-01-2020", "n1": 7, "n2": 7, "n3": 8, "n4": 9, "n5": 10, "n6": 11},
    ]
    _write_istoric(tmp_path, rows)
    monkeypatch.setattr(apd, "ROOT", tmp_path)

    with pytest.raises(ValueError):
        apd.audit_game("6/49", 11)


def test_audit_game_rejects_out_of_range_number(tmp_path, monkeypatch):
    rows = [
        {"date": "01-01-2020", "n1": 1, "n2": 2, "n3": 3, "n4": 4, "n5": 5, "n6": 50},
    ]
    _write_istoric(tmp_path, rows)
    monkeypatch.setattr(apd, "ROOT", tmp_path)

    with pytest.raises(ValueError):
        apd.audit_game("6/49", 11)
