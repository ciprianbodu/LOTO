"""Shared CSV headers and chronological training boundaries.

Without dates, the caller's row order is the chronology. With dates, sort
stably and train only on earlier days: intradraw order is not recorded.
"""

from __future__ import annotations

import re
import math

import numpy as np
import pandas as pd

DATE_NAMES = {"date", "data", "draw_date", "extragere"}


def canonical_history_columns(df: pd.DataFrame) -> pd.DataFrame:
    names = []
    for column in df.columns:
        name = str(column).strip().lower()
        if name in DATE_NAMES:
            name = "date"
        elif name != "joker" and re.fullmatch(r"n[1-9]\d*", name) is None:
            name = column
        names.append(name)
    if len(set(names)) != len(names):
        raise ValueError("coloane duplicate sau ambigue după normalizare")
    if names == list(df.columns):
        return df
    result = df.copy()
    result.columns = names
    return result


def history_dates(df: pd.DataFrame) -> pd.Series | None:
    if "date" not in df.columns:
        return None
    raw = df["date"]
    dates = pd.to_datetime(raw, format="%d-%m-%Y", errors="coerce")
    if dates.isna().any():
        iso = pd.to_datetime(raw, format="ISO8601", errors="coerce")
        dates = dates.fillna(iso)
    if dates.isna().any():
        mixed = pd.to_datetime(raw, format="mixed", dayfirst=True, errors="coerce")
        dates = dates.fillna(mixed)
    if dates.isna().any():
        raise ValueError("date de extragere lipsă sau invalide")
    return dates.dt.normalize()


def chronological_history(df: pd.DataFrame) -> pd.DataFrame:
    df = canonical_history_columns(df)
    dates = history_dates(df)
    if dates is not None and not dates.is_monotonic_increasing:
        order = np.argsort(dates.to_numpy(), kind="stable")
        return df.iloc[order].reset_index(drop=True)
    return df


def training_cutoffs(df: pd.DataFrame) -> tuple[int, ...]:
    """Exclusive prefix end per target, excluding every draw on its day."""
    dates = history_dates(df)
    if dates is None:
        return tuple(range(len(df)))
    if not dates.is_monotonic_increasing:
        raise ValueError("istoricul trebuie ordonat cronologic înainte de simulare")
    values = dates.to_numpy()
    return tuple(map(int, np.searchsorted(values, values, side="left")))


def simulation_indices(cutoffs, depth: float, step: int = 1) -> list[int]:
    """Eligible targets with at least five prior draws; shared with cache counts."""
    if not math.isfinite(float(depth)) or not 0 < depth <= 100:
        raise ValueError("backtest depth must be in (0, 100]")
    if int(step) != step or step < 1:
        raise ValueError("simulation step must be a positive integer")
    n = len(cutoffs)
    if n < 10:
        return []
    start = n - max(1, int(n * depth / 100.0))
    return [i for i in range(start, n, int(step)) if cutoffs[i] >= 5]
