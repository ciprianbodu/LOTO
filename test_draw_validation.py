"""Teste pentru contractul comun de validare a extragerilor
(`loto_enterprise.core.draw_validation.valid_draw_matrix`), consumat identic de
engine, benchmark și walk-forward (CLAUDE.md §4.1). Fisier lipsa pana acum
(verificare globala 2026-09-07) — cele patru module de contract de baza aveau
teste dedicate, in afara de acesta."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from loto_enterprise.core.draw_validation import valid_draw_matrix

COLS = ["n1", "n2", "n3", "n4", "n5", "n6"]


def _df(rows: list[list]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=COLS)


def test_all_valid_rows_pass_through():
    df = _df([[1, 2, 3, 4, 5, 6], [10, 20, 30, 40, 45, 49]])
    matrix, mask = valid_draw_matrix(df, COLS, draw_n=6, max_num=49)
    assert mask.tolist() == [True, True]
    assert matrix.shape == (2, 6)
    assert matrix.tolist() == [[1, 2, 3, 4, 5, 6], [10, 20, 30, 40, 45, 49]]


def test_duplicate_numbers_rejected():
    df = _df([[1, 2, 3, 4, 5, 5]])  # 5 apare de doua ori
    matrix, mask = valid_draw_matrix(df, COLS, draw_n=6, max_num=49)
    assert mask.tolist() == [False]
    assert matrix.shape == (0, 6)


def test_out_of_range_rejected():
    df = _df([[0, 2, 3, 4, 5, 6], [1, 2, 3, 4, 5, 50]])  # 0 si 50 in afara [1,49]
    matrix, mask = valid_draw_matrix(df, COLS, draw_n=6, max_num=49)
    assert mask.tolist() == [False, False]


def test_decimal_values_rejected_not_silently_truncated():
    """§4.1: 'zecimale nu se transforma tacit' — 2.5 nu devine 2."""
    df = _df([[1, 2, 3, 4, 5, 6.5]])
    matrix, mask = valid_draw_matrix(df, COLS, draw_n=6, max_num=49)
    assert mask.tolist() == [False]


def test_non_numeric_values_rejected():
    df = _df([[1, 2, 3, 4, 5, "abc"]])
    matrix, mask = valid_draw_matrix(df, COLS, draw_n=6, max_num=49)
    assert mask.tolist() == [False]


def test_nan_values_rejected():
    df = _df([[1, 2, 3, 4, 5, np.nan]])
    matrix, mask = valid_draw_matrix(df, COLS, draw_n=6, max_num=49)
    assert mask.tolist() == [False]


def test_mixed_valid_and_invalid_rows_only_valid_survive():
    df = _df(
        [
            [1, 2, 3, 4, 5, 6],  # valid
            [1, 2, 3, 4, 5, 5],  # duplicat
            [7, 8, 9, 10, 11, 12],  # valid
            [0, 8, 9, 10, 11, 12],  # out of range
        ]
    )
    matrix, mask = valid_draw_matrix(df, COLS, draw_n=6, max_num=49)
    assert mask.tolist() == [True, False, True, False]
    assert matrix.tolist() == [[1, 2, 3, 4, 5, 6], [7, 8, 9, 10, 11, 12]]


def test_empty_dataframe_returns_empty_matrix_correct_shape():
    df = _df([])
    matrix, mask = valid_draw_matrix(df, COLS, draw_n=6, max_num=49)
    assert matrix.shape == (0, 6)
    assert mask.shape == (0,)


def test_wrong_column_count_raises():
    df = _df([[1, 2, 3, 4, 5, 6]])
    with pytest.raises(ValueError, match="6 coloane"):
        valid_draw_matrix(df, COLS[:5], draw_n=6, max_num=49)


def test_missing_columns_raises():
    df = pd.DataFrame([[1, 2, 3]], columns=["a", "b", "c"])
    with pytest.raises(ValueError, match="lipsesc"):
        valid_draw_matrix(df, COLS[:3], draw_n=3, max_num=49)


def test_extreme_float_overflow_does_not_false_accept():
    """int64 overflow (ex. 1e20) clampeaza la INT64_MIN — trebuie respins de
    `in_range`, nu acceptat tacit ca numar valid."""
    df = _df([[1, 2, 3, 4, 5, 1e20]])
    matrix, mask = valid_draw_matrix(df, COLS, draw_n=6, max_num=49)
    assert mask.tolist() == [False]


def test_joker_urna2_single_pick_1_to_20():
    """Joker Urna 2: draw_n=1, max_num=20 — contractul e acelasi modul, nu o
    verificare separata in aval."""
    df = pd.DataFrame({"joker": [1, 20, 21, 0, 15]})
    matrix, mask = valid_draw_matrix(df, ["joker"], draw_n=1, max_num=20)
    assert mask.tolist() == [True, True, False, False, True]
    assert matrix.tolist() == [[1], [20], [15]]
