"""Copie înghețată a regulii `ens_win100+winall+ewma0.01` (ecranul din 2026-10-10).

Media rangurilor a trei trăsături pe zile de extragere, calculate numai din
zilele de dinaintea zilei țintă:
- `win100`: de câte ori a ieșit numărul în ultimele 100 de zile cu extrageri;
- `winall`: de câte ori a ieșit numărul în tot istoricul;
- `ewma0.01`: medie mobilă exponențială a aparițiilor pe zi, α = 0,01.
Rangul unei trăsături e pe zi, cu rang mediu la egalitate, pe valorile
rotunjite la 9 zecimale. Scorul e media celor trei ranguri. Ordinea pool-ului:
scor descrescător, apoi aparițiile din tot istoricul descrescător, apoi numărul
mai mare. Toate extragerile dintr-o zi au aceeași zi: o zi cu două extrageri
numără ambele în trăsături, iar niciuna nu vede cealaltă.

Fișierul nu se modifică pe durata testului; amprenta lui SHA-256 este în
`preregistration_2026-10-11.json`.
"""

from __future__ import annotations

import numpy as np
from scipy.stats import rankdata

WINDOW_DAYS = 100
EWMA_ALPHA = 0.01


def day_counts(dates, draws, max_num: int) -> np.ndarray:
    """Matricea zile × numere: aparițiile fiecărui număr în fiecare zi cu extrageri.

    `dates` sunt în ordine nedescrescătoare; rândurile aceleiași date formează
    o zi."""
    draws = np.asarray(draws, dtype=np.int64)
    if len(draws) == 0:
        return np.zeros((0, max_num))
    day = np.zeros(len(draws), dtype=np.int64)
    for i in range(1, len(draws)):
        day[i] = day[i - 1] + (dates[i] != dates[i - 1])
    C = np.zeros((int(day[-1]) + 1, max_num))
    np.add.at(C, (np.repeat(day, draws.shape[1]), draws.ravel() - 1), 1.0)
    return C


def features(C: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(win100, winall, ewma) pentru ziua de după ultima zi din `C`."""
    N = C.shape[1]
    if len(C) == 0:
        return np.zeros(N), np.zeros(N), np.zeros(N)
    win = C[-WINDOW_DAYS:].sum(axis=0)
    total = C.sum(axis=0)
    ew = np.zeros(N)
    for row in C:
        ew = EWMA_ALPHA * row + (1.0 - EWMA_ALPHA) * ew
    return win, total, ew


def rank01(values: np.ndarray) -> np.ndarray:
    """Rang mediu la egalitate pe valorile rotunjite, scalat în [0, 1]."""
    n = len(values)
    return (rankdata(np.round(values, 9), method="average") - 1.0) / max(1, n - 1)


def scores(dates, draws, max_num: int) -> dict[int, float]:
    """Scorul fiecărui număr pentru ziua de după ultima extragere din istoric."""
    win, total, ew = features(day_counts(dates, draws, max_num))
    s = (rank01(win) + rank01(total) + rank01(ew)) / 3.0
    return {n + 1: float(s[n]) for n in range(max_num)}


def ranking(dates, draws, max_num: int) -> list[int]:
    """Numerele în ordinea jucată: scor, apoi aparițiile totale, apoi numărul mai mare."""
    C = day_counts(dates, draws, max_num)
    win, total, ew = features(C)
    s = (rank01(win) + rank01(total) + rank01(ew)) / 3.0
    num = np.arange(max_num)
    order = np.lexsort((-num, -total, -s))
    return [int(n) + 1 for n in order]
