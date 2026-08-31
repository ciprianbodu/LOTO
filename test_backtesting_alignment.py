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


def test_invalid_out_of_range_duplicate_and_decimal_rows_are_excluded_everywhere(tmp_path):
    """Engine, WF și benchmark trebuie să vadă aceeași istorie validă."""
    from loto_engine import LotoEngine
    from loto_enterprise.benchmark.runner import GameDef, load_draws

    df = _clean_df(12)
    df.loc[2, "n1"] = 0
    df.loc[4, "n2"] = df.loc[4, "n1"]
    df["n3"] = df["n3"].astype(float)
    df.loc[7, "n3"] = 3.5
    path = tmp_path / "loto_6_49.csv"
    df.to_csv(path, index=False)

    game = GameDef("loto_6_49", "Loto 6/49", str(path), COLS, 49, 6)
    bench_draws = load_draws(game)
    backtester = LotoBacktester(df, game_type="6/49")
    engine = LotoEngine("6/49")

    assert engine.load_data(str(path))
    assert len(bench_draws) == len(backtester.draws) == len(engine.data) == len(df) - 3
    assert engine.audit["invalid_draw_rows_dropped"] == 3


def test_engine_rejects_a_dataset_with_no_valid_draws(tmp_path):
    from loto_engine import LotoEngine

    df = _clean_df(10)
    df["n1"] = 0
    path = tmp_path / "all_invalid.csv"
    df.to_csv(path, index=False)

    engine = LotoEngine("6/49")
    assert not engine.load_data(str(path))
    assert engine.data is None
    assert engine.audit["rows_loaded"] == 10
    assert engine.audit["rows_valid"] == 0


def test_joker_urna2_rejects_decimal_out_of_range_and_missing_values(tmp_path):
    """Urna 2 are propria validare 1..20; 4.7 nu devine tăcut bila 4."""
    from loto_engine import LotoEngine

    rows = []
    joker_values = [1, 4.7, 21, np.nan, 4, 20]
    for i, joker in enumerate(joker_values):
        base = i * 5 + 1
        rows.append({
            "date": f"{i + 1:02d}-01-2026",
            "n1": base, "n2": base + 1, "n3": base + 2,
            "n4": base + 3, "n5": base + 4,
            "joker": joker,
        })
    path = tmp_path / "joker.csv"
    pd.DataFrame(rows).to_csv(path, index=False)

    engine = LotoEngine("joker")
    assert engine.load_data(str(path))

    assert engine._valid_joker_values().tolist() == [1, 4, 20]
    assert engine.audit["joker_urna2_rows_total"] == 6
    assert engine.audit["joker_urna2_rows_valid"] == 3
    assert engine.audit["joker_urna2_invalid_rows_dropped"] == 3
    freq = engine.analyze_joker_frequency()
    assert freq[0] == freq[3] == freq[19] == 1
    assert int(freq.sum()) == 3


def test_joker_urna2_without_valid_values_has_no_arbitrary_frequency_fallback(tmp_path):
    """Lipsa Urnei 2 nu trebuie să dea un clasament plat și bila 20 din tie-break."""
    from loto_engine import LotoEngine

    df = pd.DataFrame([
        {"date": f"{i + 1:02d}-01-2026", "n1": 1, "n2": 2, "n3": 3,
         "n4": 4, "n5": 5, "joker": value}
        for i, value in enumerate([0, 20.2, np.nan, 21, "bad"])
    ])
    path = tmp_path / "joker_invalid_urna2.csv"
    df.to_csv(path, index=False)

    engine = LotoEngine("joker")
    assert engine.load_data(str(path))
    assert engine.analyze_joker_frequency().sum() == 0
    assert engine._frequency_fallback_scores(is_joker_drum=True) == {}
