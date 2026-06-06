"""Metode de scoring bazate pe GREEDY SET-COVER (acoperire submodulară).

Filosofie DISTINCTĂ față de frecvență: nu contează doar cât de des apare un
număr, ci cât de mult ACOPERĂ extrageri care nu sunt deja acoperite. Folosim
acoperire cu randamente descrescătoare (al doilea număr dintr-o extragere
valorează mai puțin decât primul) → favorizează un set DIVERS, întins, exact ca
wheeling-ul (set-cover) din pipeline, dar aplicat ca scorer per-număr.

Greedy set-cover e secvențial și pe univers mic (≤49) → CPU e alegerea corectă
(GPU n-ar accelera un matvec 49×n). Determinist, fără antrenare.

Onestitate: pe loterie aleatoare nu prezice — concurează în benchmark; dacă nu
bate random la 4+, decizia nu îl alege.
"""
from __future__ import annotations

import logging
from typing import Callable, Dict, Tuple

import numpy as np

logger = logging.getLogger(__name__)


def _normalize(scores: Dict[int, float], max_num: int) -> Dict[int, float]:
    if not scores:
        return {n: 0.0 for n in range(1, max_num + 1)}
    vals = np.fromiter(scores.values(), dtype=np.float64)
    vmin, vmax = float(vals.min()), float(vals.max())
    rng = max(vmax - vmin, 1e-12)
    out = {int(k): float((v - vmin) / rng) for k, v in scores.items()}
    for n in range(1, max_num + 1):
        out.setdefault(n, 0.0)
    return out


def _binary(draws_2d: np.ndarray, max_num: int) -> np.ndarray:
    """(max_num, n_draws) indicator 0/1."""
    n = draws_2d.shape[0]
    B = np.zeros((max_num, n), dtype=np.float64)
    for i, row in enumerate(draws_2d):
        for v in row:
            vi = int(v)
            if 1 <= vi <= max_num:
                B[vi - 1, i] = 1.0
    return B


def _greedy_cover(B: np.ndarray, draw_w: np.ndarray, r: float = 0.5) -> np.ndarray:
    """Greedy submodular max-coverage. Întoarce scor per număr = câștigul marginal
    de acoperire LA MOMENTUL selecției (descrescător, randamente diminuate).

    Fiecare extragere t acoperită de c numere selectate valorează draw_w[t]*(1-r^c).
    Câștigul marginal al unui număr nou pe extragerea t = draw_w[t]*(1-r)*r^c_t.
    """
    max_num, n = B.shape
    c = np.zeros(n, dtype=np.float64)          # câte numere selectate acoperă fiecare extragere
    selected = np.zeros(max_num, dtype=bool)
    scores = np.zeros(max_num, dtype=np.float64)
    for _step in range(max_num):
        g = draw_w * (1.0 - r) * np.power(r, c)   # valoarea marginală/extragere a unui cover în plus
        gain = B @ g                              # (max_num,) câștig per număr candidat
        gain[selected] = -np.inf
        idx = int(np.argmax(gain))
        if not np.isfinite(gain[idx]):
            break
        scores[idx] = max(gain[idx], 0.0)
        selected[idx] = True
        c = c + B[idx]                            # selecția lui idx adaugă un cover extragerilor lui
    return scores


def score_cover_greedy(draws_2d: np.ndarray, max_num: int) -> Dict[int, float]:
    """Greedy max-coverage cu ponderare de recență (extragerile recente cântăresc
    mai mult). Favorizează numere care acoperă extrageri DIVERSE recente."""
    n = draws_2d.shape[0]
    if n < 10:
        return {}
    B = _binary(draws_2d, max_num)
    draw_w = np.exp(np.linspace(-2.0, 0.0, n))   # recență: recent = greu
    scores = _greedy_cover(B, draw_w)
    return _normalize({i + 1: float(scores[i]) for i in range(max_num)}, max_num)


def score_cover_rarity(draws_2d: np.ndarray, max_num: int) -> Dict[int, float]:
    """Greedy cover unde fiecare extragere e ponderată cu RARITATEA ei (inversul
    frecvenței globale a numerelor sale) × recență → acoperă combinațiile rare,
    scoțând la suprafață numere care apar în extrageri neobișnuite."""
    n = draws_2d.shape[0]
    if n < 10:
        return {}
    B = _binary(draws_2d, max_num)               # (max_num, n)
    freq = B.sum(axis=1) + 1.0                    # frecvența globală per număr (+1 smoothing)
    inv = 1.0 / freq                              # raritate per număr
    # raritatea unei extrageri = suma rarităților numerelor ei (combinații rare → mare)
    draw_rarity = (B * inv[:, None]).sum(axis=0)  # (n,)
    rec = np.exp(np.linspace(-2.0, 0.0, n))
    draw_w = draw_rarity * rec
    if draw_w.sum() <= 0:
        draw_w = rec
    scores = _greedy_cover(B, draw_w)
    return _normalize({i + 1: float(scores[i]) for i in range(max_num)}, max_num)


def score_winslips(draws_2d: np.ndarray, max_num: int) -> Dict[int, float]:
    """Scorer stil WinSlips: acoperire de tip ROATĂ ABREVIATĂ pe PERECHI (covering
    design, t=2). WinSlips garantează acoperirea t-subseturilor pool-ului; aici
    scorăm numerele după contribuția la acoperirea PERECHILOR frecvente din istoric.

    Acoperire submodulară pe perechi: o pereche (i,j) cu pondere P[i,j] valorează
    a1 cu un capăt selectat și 1.0 cu ambele (a1=0.6 → al doilea capăt aduce 0.4 <
    0.6 = randamente descrescătoare). Greedy: la fiecare pas alegem numărul cu cel
    mai mare câștig marginal de acoperire de perechi. Efectul: roata acoperă cât
    mai multe perechi DISTINCTE (diversitate), exact logica covering-design.

    Independent (numpy), determinist, fără antrenare.
    """
    n = draws_2d.shape[0]
    if n < 10:
        return {}
    B = _binary(draws_2d, max_num)               # (max_num, n)
    rec = np.exp(np.linspace(-2.0, 0.0, n))       # recență
    # Matrice de co-apariție ponderată: P[i,j] = suma ponderilor extragerilor în
    # care i ȘI j apar împreună (= „perechile" pe care roata vrea să le acopere).
    Bw = B * rec[None, :]
    P = Bw @ B.T                                  # (max_num, max_num)
    np.fill_diagonal(P, 0.0)
    Sall = P.sum(axis=1)                          # pondere totală de perechi per număr
    a1 = 0.6                                      # valoarea primului capăt (al doilea = 1-a1=0.4)
    sel = np.zeros(max_num, dtype=bool)
    Ssel = np.zeros(max_num, dtype=np.float64)    # P[i, selectate]
    scores = np.zeros(max_num, dtype=np.float64)
    for _step in range(max_num):
        # câștig marginal = a1·(perechi cu capăt neselectat) + (1-a1)·(perechi completate)
        gain = a1 * (Sall - Ssel) + (1.0 - a1) * Ssel
        gain[sel] = -np.inf
        idx = int(np.argmax(gain))
        if not np.isfinite(gain[idx]):
            break
        scores[idx] = max(gain[idx], 0.0)
        sel[idx] = True
        Ssel = Ssel + P[:, idx]                   # idx selectat → actualizează perechile cu idx
    return _normalize({i + 1: float(scores[i]) for i in range(max_num)}, max_num)


# ===========================================================================
# Registry
# ===========================================================================
COVERAGE_METHODS: Dict[str, Tuple[Callable, str, bool, str]] = {
    "cover_greedy": (score_cover_greedy, "coverage", False,
                     "greedy set-cover submodular (recență) — acoperire diversă · CPU"),
    "cover_rarity": (score_cover_rarity, "coverage", False,
                     "greedy cover ponderat pe raritatea extragerilor — combinații rare · CPU"),
    "winslips": (score_winslips, "coverage", False,
                 "stil WinSlips: acoperire roată abreviată pe perechi (covering design t=2) · CPU"),
}
