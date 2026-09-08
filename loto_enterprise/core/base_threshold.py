"""Rata exacta de `target`+ hituri pentru un pool restrans la un interval de numere.

Sursa unica pentru tabelul afisat in UI (submeniul de sub restrangerea bazei) si
pentru diagnosticul din `scripts/analysis/bench_base_threshold.py`.

Rata NU este simulata. Pentru fiecare extragere se numara cate dintre numerele
iesite au cazut in interval; de acolo probabilitatea ca un pool de `pool_size`
numere trase din acel interval sa prinda cel putin `target` este hipergeometrica.
Media peste extrageri este rata exacta a intervalului, fara zgomot Monte Carlo.

Ce arata tabelul si ce NU arata: un pool de dimensiune fixa are aceeasi
probabilitate teoretica indiferent care numere il compun (`theoretical_rate`).
Diferentele dintre intervale sunt abateri empirice ale istoricului, iar cu cat
intervalul e mai ingust cu atat raman mai putine pool-uri distincte si cu atat
rata masurata e mai zgomotoasa — la latime egala cu `pool_size` exista o singura
combinatie posibila, deci „rata" ei este istoricul acelei combinatii. De aceea
`interval_table` intoarce, pentru fiecare latime, si cel mai bun interval gasit
pe extrageri sintetice uniforme: campionul apare in orice set de date, inclusiv
acolo unde nu exista nimic de gasit. Vezi CLAUDE.md §6.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import comb

import numpy as np

__all__ = [
    "IntervalRow",
    "best_interval",
    "interval_rate",
    "interval_table",
    "synthetic_draws",
    "theoretical_rate",
]


def theoretical_rate(
    max_num: int, pool_size: int, draw_n: int, target: int = 3
) -> float:
    """Procentul teoretic de `target`+ pentru ORICE pool de `pool_size` numere.

    Hipergeometric pur, independent de care numere compun pool-ul. Este
    referinta fata de care se citeste orice rand din tabel.
    """
    if pool_size <= 0 or pool_size > max_num or draw_n <= 0 or draw_n > max_num:
        raise ValueError("geometrie invalida pentru rata teoretica")
    favourable = sum(
        comb(pool_size, h) * comb(max_num - pool_size, draw_n - h)
        for h in range(target, min(pool_size, draw_n) + 1)
    )
    return favourable / comb(max_num, draw_n) * 100


def interval_rate(
    draws: np.ndarray, lo: int, hi: int, pool_size: int, target: int = 3
) -> float:
    """Rata exacta de `target`+, mediata peste TOATE pool-urile din `[lo..hi]`.

    `draws` este matricea (n_extrageri, draw_n) de numere valide.
    """
    if lo < 1 or hi < lo:
        raise ValueError(f"interval invalid ({lo}..{hi})")
    span = hi - lo + 1
    if span < pool_size:
        raise ValueError(
            f"intervalul {lo}..{hi} nu incape un pool de {pool_size} numere"
        )
    if draws.size == 0:
        raise ValueError("nu exista extrageri")
    inside = ((draws >= lo) & (draws <= hi)).sum(axis=1)
    total = comb(span, pool_size)
    acc = 0.0
    for value in np.unique(inside):
        hits = int(value)
        favourable = sum(
            comb(hits, h) * comb(span - hits, pool_size - h)
            for h in range(target, min(hits, pool_size) + 1)
        )
        acc += favourable / total * int((inside == value).sum())
    return acc / len(inside) * 100


def best_interval(
    draws: np.ndarray, max_num: int, width: int, pool_size: int, target: int = 3
) -> tuple[int, int, float]:
    """Intervalul de latimea `width` cu cea mai mare rata pe `draws`.

    La egalitate castiga capatul de jos cel mai mic, ca rezultatul sa nu depinda
    de ordinea de parcurgere.
    """
    if width < pool_size or width > max_num:
        raise ValueError(f"latime invalida ({width})")
    best = max(
        (
            (interval_rate(draws, lo, lo + width - 1, pool_size, target), -lo)
            for lo in range(1, max_num - width + 2)
        )
    )
    rate, neg_lo = best
    lo = -neg_lo
    return lo, lo + width - 1, rate


def synthetic_draws(
    n_draws: int, draw_n: int, max_num: int, seed: int = 500
) -> np.ndarray:
    """Extrageri uniforme, pentru coloana de control a tabelului.

    Fiecare numar are exact aceeasi sansa, deci orice campion din coloana asta
    este zgomot prin constructie.
    """
    rng = np.random.default_rng(seed)
    return np.array(
        [
            rng.choice(np.arange(1, max_num + 1), size=draw_n, replace=False)
            for _ in range(n_draws)
        ]
    )


@dataclass(frozen=True)
class IntervalRow:
    """Cel mai bun interval de o anumita latime, real si pe control."""

    width: int
    lo: int
    hi: int
    whole: float
    first_half: float
    second_half: float
    control_lo: int
    control_hi: int
    control: float

    @property
    def label(self) -> str:
        return f"{self.lo}–{self.hi}"

    @property
    def control_label(self) -> str:
        return f"{self.control_lo}–{self.control_hi}"


def interval_table(
    draws: np.ndarray,
    max_num: int,
    pool_size: int,
    draw_n: int,
    target: int = 3,
    seed: int = 500,
) -> list[IntervalRow]:
    """Cate un rand per latime de interval, de la `pool_size` la `max_num`.

    Pentru fiecare latime: cel mai bun interval pe tot istoricul, ratele lui pe
    cele doua jumatati, si cel mai bun interval de aceeasi latime gasit pe
    extrageri uniforme. Ultimul rand (latime == max_num) este jocul nerestrans,
    unde toate coloanele cad pe rata teoretica.
    """
    half = len(draws) // 2
    if half < 1:
        raise ValueError("istoric prea scurt pentru tabel")
    control = synthetic_draws(len(draws), draw_n, max_num, seed=seed)
    rows = []
    for width in range(pool_size, max_num + 1):
        lo, hi, whole = best_interval(draws, max_num, width, pool_size, target)
        c_lo, c_hi, c_rate = best_interval(
            control, max_num, width, pool_size, target
        )
        rows.append(
            IntervalRow(
                width=width,
                lo=lo,
                hi=hi,
                whole=whole,
                first_half=interval_rate(draws[:half], lo, hi, pool_size, target),
                second_half=interval_rate(draws[half:], lo, hi, pool_size, target),
                control_lo=c_lo,
                control_hi=c_hi,
                control=c_rate,
            )
        )
    return rows
