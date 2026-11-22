"""Recurrence of inclusive main-ball intervals, measured at historical forecast origins."""

from bisect import bisect_left
from statistics import median

import numpy as np
import pandas as pd


def _phase(pause, calendar):
    if pause is None:
        return None
    if calendar:
        return ("0–7 zile", "8–14 zile", "15–28 zile", "29+ zile")[bisect_left((7, 14, 28), pause)]
    return ("0–2 extrageri", "3–6 extrageri", "7–13 extrageri", "14+ extrageri")[bisect_left((2, 6, 13), pause)]


def range_timing(draws, dates, limits):
    """Describe the last 100 draws and compute the pause known before each draw day.

    The latest known occurrence may predate the reporting window. Median gaps
    use only pairs inside that window. No occurrence means an unknown pause.
    """
    low, high = limits
    matrix = np.asarray(draws)
    if matrix.ndim != 2 or matrix.shape[1] == 0 or low > high:
        raise ValueError("Matricea extragerilor sau intervalul este invalid.")
    size = len(matrix)
    calendar = False
    days = []
    if dates is not None:
        if len(dates) != size:
            raise ValueError("Datele calendaristice și extragerile trebuie să aibă aceeași lungime.")
        days = [pd.Timestamp(value).date() if pd.notna(value) else None for value in dates]
        calendar = all(day is not None for day in days) and all(a <= b for a, b in zip(days, days[1:]))

    hits = ((matrix >= low) & (matrix <= high)).all(axis=1)
    start = max(0, size - 100)
    indices = [int(i) for i in np.flatnonzero(hits[start:]) + start]
    events = [{"draw_index": i, "date": days[i].isoformat() if calendar else None} for i in indices]
    gaps = [b - a for a, b in zip(indices, indices[1:])]
    day_gaps = [(days[b] - days[a]).days for a, b in zip(indices, indices[1:])] if calendar else []

    last_hit = None
    phase_keys = []
    for i, hit in enumerate(hits):
        if not calendar or i == 0 or days[i] != days[i - 1]:
            # All draws on a date share the information available the day before.
            pause = None
            if last_hit is not None:
                pause = (days[i - 1] - days[last_hit]).days if calendar else i - 1 - last_hit
            phase = _phase(pause, calendar)
        phase_keys.append((phase, days[i] if calendar else i))
        if hit:
            last_hit = i

    since = size - 1 - last_hit if last_hit is not None else None
    days_since = (days[-1] - days[last_hit]).days if calendar and last_hit is not None else None
    return {
        "window_draws": min(size, 100),
        "occurrences": len(indices),
        "events": events,
        "since_as_of": days[-1].isoformat() if calendar and size else None,
        "last_date": days[last_hit].isoformat() if calendar and last_hit is not None else None,
        "draws_since": since,
        "days_since": days_since,
        "median_gap_draws": median(gaps) if gaps else None,
        "median_gap_days": median(day_gaps) if day_gaps else None,
        "phase_basis": "days" if calendar else "draws",
        "current_phase": _phase(days_since if calendar else since, calendar),
        "_phase_keys": phase_keys,
    }


def phase_evidence(timing, paired_deltas):
    """Compare tickets only at past origins in today's pause band, counting ties."""
    keys = timing["_phase_keys"]
    if len(paired_deltas) != len(keys):
        raise ValueError("Rezultatele pereche trebuie aliniate cu istoricul extragerilor.")
    current_phase = timing["current_phase"]
    periods = set()
    wins = losses = compared = 0
    for (phase, period), delta in zip(keys, paired_deltas, strict=True):
        if current_phase is None or phase != current_phase or delta is None:
            continue
        periods.add(period)
        compared += 1
        wins += int(delta > 0)
        losses += int(delta < 0)
    sufficient = len(periods) >= 8
    return {
        "basis": timing["phase_basis"],
        "phase": current_phase,
        "compared": compared,
        "periods": len(periods),
        "wins": wins,
        "losses": losses,
        "delta": wins - losses,
        "sufficient": sufficient,
        "supports": sufficient and wins >= losses,
    }
