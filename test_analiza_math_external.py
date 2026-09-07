"""Teste pentru analiza_math_external.py — trei goluri:
- `_load_csv` nu verifica duplicate intr-un rand, acum foloseste
  `draw_validation.valid_draw_matrix`.
- `hyper_p_ge` deleaga acum la `decision.expected_random_rate`.
- poarta "bate baseline-ul" era un prag slab (`n >= floor(exp)+1`), acum
  foloseste limita inferioara Wilson, ca decizia de productie."""
from __future__ import annotations

import pandas as pd
import pytest

import analiza_math_external as ame
from loto_enterprise.benchmark.decision import expected_random_rate


def test_load_csv_rejects_row_with_duplicate_numbers(tmp_path):
    csv_path = tmp_path / "draws.csv"
    pd.DataFrame([
        {"n1": 1, "n2": 2, "n3": 3, "n4": 4, "n5": 5, "n6": 6},
        {"n1": 7, "n2": 7, "n3": 8, "n4": 9, "n5": 10, "n6": 11},  # duplicat n1==n2
    ]).to_csv(csv_path, index=False)

    draws = ame._load_csv(csv_path, ("n1", "n2", "n3", "n4", "n5", "n6"), 49)
    assert draws.shape[0] == 1  # doar randul valid


def test_load_csv_rejects_decimal_value(tmp_path):
    csv_path = tmp_path / "draws.csv"
    pd.DataFrame([
        {"n1": 1, "n2": 2, "n3": 3, "n4": 4, "n5": 5.7, "n6": 6},
    ]).to_csv(csv_path, index=False)

    with pytest.raises(RuntimeError, match="gol"):
        ame._load_csv(csv_path, ("n1", "n2", "n3", "n4", "n5", "n6"), 49)


def test_load_csv_accepts_clean_rows(tmp_path):
    csv_path = tmp_path / "draws.csv"
    pd.DataFrame([
        {"n1": 1, "n2": 2, "n3": 3, "n4": 4, "n5": 5, "n6": 6},
        {"n1": 10, "n2": 20, "n3": 30, "n4": 40, "n5": 44, "n6": 49},
    ]).to_csv(csv_path, index=False)

    draws = ame._load_csv(csv_path, ("n1", "n2", "n3", "n4", "n5", "n6"), 49)
    assert draws.shape == (2, 6)


def test_hyper_p_ge_matches_canonical_expected_random_rate():
    for target, universe, draw_n, pool in [(3, 49, 6, 11), (4, 40, 5, 16), (1, 20, 1, 1)]:
        assert ame.hyper_p_ge(target, universe, draw_n, pool) == pytest.approx(
            expected_random_rate(universe, draw_n, pool, target)
        )


def test_eval_one_uses_wilson_lower_bound_not_raw_plus_one_threshold(tmp_path, monkeypatch):
    """O metoda cu EXACT un eveniment peste asteptare (vechiul prag) nu mai
    trece automat -- Wilson cere o marja mai solida."""
    csv_path = tmp_path / "loto_6_49.csv"
    n_rows = 300
    rows = [{"n1": 1, "n2": 2, "n3": 3, "n4": 4, "n5": 5, "n6": 6 + (i % 40)} for i in range(n_rows)]
    pd.DataFrame(rows).to_csv(csv_path, index=False)

    def _fake_call_method(method, history, max_num):
        return {n: 1.0 for n in range(1, max_num + 1)}, 0.0

    monkeypatch.setattr(ame, "call_method", _fake_call_method)

    args = ("loto_6_49", "fake_flat", str(csv_path), ("n1", "n2", "n3", "n4", "n5", "n6"), 49, 6, 11)
    res = ame._eval_one(args)
    assert res["ok"] is True
    # wilson_lbN <= rateN mereu (limita inferioara nu poate depasi rata bruta observata)
    assert res["wilson_lb3"] <= res["rate3"] + 1e-9
    assert res["wilson_lb1"] <= res["rate1"] + 1e-9
    # beat3 trebuie sa reflecte EXACT testul Wilson, nu pragul vechi floor(exp)+1
    assert res["beat3"] == (res["wilson_lb3"] > res["p3"])
    assert res["beat1"] == (res["wilson_lb1"] > res["p1"])


def test_min_extra_4plus_constant_removed():
    """Constanta era legata de pragul vechi (floor(exp)+2), inlocuit de Wilson."""
    assert not hasattr(ame, "MIN_EXTRA_4PLUS_IF_NO_3")
