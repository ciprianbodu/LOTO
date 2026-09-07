"""Teste pentru analiza_4plus.py — verificare globala 2026-09-07.

Raporta "MAXIM absolut" (best-of-many celule metoda x procent) fara nicio
referinta de baseline si fara avertisment de testare multipla -- exact
tiparul "selectie dupa rezultat / multiple testing necontrolat" pe care
CLAUDE.md §12 (P3) il respinge. Acum afiseaza baseline-ul hipergeometric
teoretic (aceeasi conventie ca decision.py) si un avertisment explicit."""
from __future__ import annotations

import pandas as pd

import analiza_4plus as a4p


def _write_folds(path, rows):
    pd.DataFrame(rows).to_csv(path, index=False)


def test_main_prints_theoretical_baseline_and_multiple_testing_caveat(tmp_path, monkeypatch, capsys):
    folds = tmp_path / "folds.csv"
    rows = [
        {"game": "loto_6_49", "method": "random", "is_random": False, "failed": False,
         "percentile": 10, "rate_4plus": 0.001, "family": "baseline"},
        {"game": "loto_6_49", "method": "m1", "is_random": False, "failed": False,
         "percentile": 10, "rate_4plus": 0.02, "family": "math"},
        {"game": "loto_6_49", "method": "m2", "is_random": False, "failed": False,
         "percentile": 30, "rate_4plus": 0.005, "family": "math"},
    ]
    _write_folds(folds, rows)
    monkeypatch.setattr(a4p, "FOLDS", folds)

    rc = a4p.main()
    out = capsys.readouterr().out
    assert rc == 0
    assert "baseline random (hipergeometric" in out
    assert "testare multipla" in out
    assert "MAXIM absolut: m1" in out


def test_main_handles_missing_folds(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(a4p, "FOLDS", tmp_path / "nope.csv")
    rc = a4p.main()
    assert rc == 1
