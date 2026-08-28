"""Walk-forward cache key must change when history outside the recent tail changes."""
from __future__ import annotations

import pandas as pd

from loto_enterprise.core.walk_forward_adapter import CACHE_VERSION, _csv_hash


def test_csv_hash_changes_when_old_rows_change():
    cols = ["n1", "n2", "n3", "n4", "n5", "n6"]
    rows = [[i, i + 1, i + 2, i + 3, i + 4, i + 5] for i in range(600)]
    df1 = pd.DataFrame(rows, columns=cols)
    rows2 = list(rows)
    rows2[0] = [9, 9, 9, 9, 9, 9]  # în afara ultimelor 500
    df2 = pd.DataFrame(rows2, columns=cols)
    assert _csv_hash(df1, "6/49") != _csv_hash(df2, "6/49")


def test_csv_hash_stable_on_identical_history():
    cols = ["n1", "n2", "n3", "n4", "n5", "n6"]
    rows = [[i, i + 1, i + 2, i + 3, i + 4, i + 5] for i in range(80)]
    df_a = pd.DataFrame(rows, columns=cols)
    df_b = pd.DataFrame(rows, columns=cols)
    assert _csv_hash(df_a, "6/49") == _csv_hash(df_b, "6/49")


def test_cache_version_bumped_for_full_history_hash():
    assert CACHE_VERSION == "v17"
