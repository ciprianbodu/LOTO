"""Aliniere df ↔ draws în LotoBacktester._load_data.

Rândurile invalide (NaN/non-numerice) intră în CSV dar nu în `draws`; fără
filtrarea df-ului, `sim_idx` indexa două axe diferite: `df.iloc[:sim_idx]`
(istoric de antrenare) și `draws[sim_idx]` (ținta) — un singur rând murdar
deplasa fereastra. Pe CSV curat filtrarea trebuie să fie NO-OP.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from loto_enterprise.core.backtesting import LotoBacktester, pool_draw_hits

COLS = ["n1", "n2", "n3", "n4", "n5", "n6"]


def _clean_df(n: int = 30) -> pd.DataFrame:
    rows = []
    for i in range(n):
        base = (i * 7) % 40
        rows.append({"date": f"{(i % 28) + 1:02d}-01-2026",
                     **{c: base + j + 1 for j, c in enumerate(COLS)}})
    return pd.DataFrame(rows)


def test_clean_csv_is_noop():
    df = _clean_df()
    bt = LotoBacktester(df, game_type="6/49")  # constructorul apelează _load_data()
    assert len(bt.df) == len(df) == len(bt.draws) == len(bt.dates)
    pd.testing.assert_frame_equal(bt.df, df)


def test_dirty_rows_filtered_and_aligned():
    df = _clean_df()
    # rând complet invalid (NaN) în mijloc + unul cu prea puține numere valide
    df.loc[9, COLS] = np.nan
    df.loc[17, COLS[1:]] = np.nan
    bt = LotoBacktester(df, game_type="6/49")  # constructorul apelează _load_data()
    # df-ul filtrat trebuie să aibă EXACT atâtea rânduri câte extrageri valide
    assert len(bt.df) == len(bt.draws) == len(df) - 2
    # și fiecare poziție k din draws trebuie să corespundă rândului k din df
    for k in (0, 8, 9, 15, 20, len(bt.draws) - 1):
        expected = [int(bt.df.iloc[k][c]) for c in COLS]
        assert bt.draws[k] == expected, f"dezaliniere la sim_idx={k}"


def test_dates_stay_aligned_with_draws():
    df = _clean_df()
    df.loc[3, COLS] = np.nan
    bt = LotoBacktester(df, game_type="6/49")  # constructorul apelează _load_data()
    assert len(bt.dates) == len(bt.draws) == len(bt.df)
    for k in (0, 3, 10):
        assert bt.dates[k] == str(bt.df.iloc[k]["date"])


def test_pool_draw_hits_uses_hard_core_not_ticket_union():
    """WF hits_union = pool ∩ extragere, chiar dacă biletele omit un număr din pool."""
    pool = [1, 2, 3, 4, 5, 10]
    actual = [1, 2, 3, 20, 21, 22]
    assert pool_draw_hits(pool, actual) == 3
    # uniunea biletelor ar fi 2 dacă 3 nu e pe niciun ticket
    tickets_union = {1, 2, 10}
    assert len(tickets_union & set(actual)) == 2
    assert pool_draw_hits(pool, actual) > len(tickets_union & set(actual))
