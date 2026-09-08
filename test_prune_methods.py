"""Teste pentru prune_methods.py — scrie in disabled_methods.json, merge-only
si ireversibil (§4.3)."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

import prune_methods as pm
from loto_enterprise.benchmark import disabled as disabled_mod


def test_method_score_averages_across_rows_not_global_max():
    """Regresie: scorul NU mai e maximul global (o singura celula norocoasa
    dintr-un joc/fereastra), ci media maximelor PE RAND (peste jocuri/ferestre).
    Randul 1: cel mai bun pool e 0.30. Randul 2: cel mai bun pool e 0.10.
    Media asteptata: 0.20 — NU 0.30 (vechiul comportament, nanmax global)."""
    sub = pd.DataFrame(
        {
            "rate_4plus_k11": [0.05, 0.10],
            "rate_4plus_k16": [0.30, 0.02],
        }
    )
    assert pm._method_score(sub) == pytest.approx(0.20)


def test_method_score_ignores_nan_within_a_row():
    sub = pd.DataFrame(
        {
            "rate_4plus_k11": [np.nan, 0.10],
            "rate_4plus_k16": [0.30, np.nan],
        }
    )
    # rand 1: doar k16=0.30 valid -> max 0.30. rand 2: doar k11=0.10 -> max 0.10.
    assert pm._method_score(sub) == pytest.approx(0.20)


def test_method_score_falls_back_to_avg_hits_topk():
    sub = pd.DataFrame({"avg_hits_topk": [1.0, 2.0, 3.0]})
    assert pm._method_score(sub) == pytest.approx(2.0)


def test_method_score_empty_returns_zero():
    sub = pd.DataFrame({"other_col": [1, 2, 3]})
    assert pm._method_score(sub) == 0.0


def _write_folds(path, games):
    rows = []
    for g in games:
        rows.append(
            {"method": "m1", "game": g, "is_random": False, "rate_4plus_k11": 0.15}
        )
        rows.append(
            {"method": "m2", "game": g, "is_random": False, "rate_4plus_k11": 0.05}
        )
    pd.DataFrame(rows).to_csv(path, index=False)


def test_apply_refuses_on_incomplete_games_without_force(tmp_path, monkeypatch, capsys):
    folds = tmp_path / "folds.csv"
    _write_folds(folds, ["loto_6_49"])  # lipsesc loto_5_40, joker_urna1, joker_urna2

    class _FakeGame:
        def __init__(self, key):
            self.key = key

    monkeypatch.setattr(
        "loto_enterprise.benchmark.runner.discover_games",
        lambda: [
            _FakeGame(k)
            for k in ("loto_6_49", "loto_5_40", "joker_urna1", "joker_urna2")
        ],
    )
    disabled_path = tmp_path / "disabled_methods.json"
    monkeypatch.setattr(disabled_mod, "_PATH", disabled_path)
    monkeypatch.setattr(
        "sys.argv", ["prune_methods.py", "--folds", str(folds), "--apply"]
    )

    rc = pm.main()
    assert rc == 2
    assert not disabled_path.exists()
    out = capsys.readouterr().err
    assert "lipsesc" in out.lower() or "EROARE" in out


def test_apply_proceeds_with_force_incomplete(tmp_path, monkeypatch):
    folds = tmp_path / "folds.csv"
    _write_folds(folds, ["loto_6_49"])

    class _FakeGame:
        def __init__(self, key):
            self.key = key

    monkeypatch.setattr(
        "loto_enterprise.benchmark.runner.discover_games",
        lambda: [
            _FakeGame(k)
            for k in ("loto_6_49", "loto_5_40", "joker_urna1", "joker_urna2")
        ],
    )
    disabled_path = tmp_path / "disabled_methods.json"
    monkeypatch.setattr(disabled_mod, "_PATH", disabled_path)
    monkeypatch.setattr(
        "sys.argv",
        ["prune_methods.py", "--folds", str(folds), "--apply", "--force-incomplete"],
    )

    rc = pm.main()
    assert rc == 0
    assert disabled_path.exists()
    data = json.loads(disabled_path.read_text(encoding="utf-8"))
    assert "m2" in data["disabled"]  # scor mai mic -> bottom 50% -> legendat


def test_apply_proceeds_when_all_games_present(tmp_path, monkeypatch):
    folds = tmp_path / "folds.csv"
    games = ["loto_6_49", "loto_5_40", "joker_urna1", "joker_urna2"]
    _write_folds(folds, games)

    class _FakeGame:
        def __init__(self, key):
            self.key = key

    monkeypatch.setattr(
        "loto_enterprise.benchmark.runner.discover_games",
        lambda: [_FakeGame(k) for k in games],
    )
    disabled_path = tmp_path / "disabled_methods.json"
    monkeypatch.setattr(disabled_mod, "_PATH", disabled_path)
    monkeypatch.setattr(
        "sys.argv", ["prune_methods.py", "--folds", str(folds), "--apply"]
    )

    rc = pm.main()
    assert rc == 0
    assert disabled_path.exists()
