"""Metode de scoring bazate pe COVER DESIGN — acoperire combinatorică.

Filosofie DISTINCTĂ față de frecvență: nu contează doar cât de des apare un
număr, ci cât de mult ACOPERĂ extrageri care nu sunt deja acoperite. Folosim
acoperire cu randamente descrescătoare (al doilea număr dintr-o extragere
valorează mai puțin decât primul) → favorizează un set DIVERS, întins, exact ca
wheeling-ul (set-cover) din pipeline, dar aplicat ca scorer per-număr.

Greedy set-cover e secvențial și pe univers mic (≤49) → CPU e alegerea corectă
(GPU n-ar accelera un matvec 49×n). Determinist, fără antrenare.

Onestitate: pe loterie aleatoare nu prezic — concurează în benchmark; dacă nu
bat random la 4+, decizia nu le alege.
"""
from __future__ import annotations

import logging
from typing import Callable

import numpy as np

from .score_normalize import normalize_scores as _normalize

logger = logging.getLogger(__name__)


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
    c = np.zeros(n, dtype=np.float64)
    selected = np.zeros(max_num, dtype=bool)
    scores = np.zeros(max_num, dtype=np.float64)
    for _step in range(max_num):
        g = draw_w * (1.0 - r) * np.power(r, c)
        gain = B @ g
        gain[selected] = -np.inf
        idx = int(np.argmax(gain))
        if not np.isfinite(gain[idx]):
            break
        scores[idx] = max(gain[idx], 0.0)
        selected[idx] = True
        c = c + B[idx]
    return scores


# ===========================================================================
# Metode originale (3)
# ===========================================================================

def score_cover_greedy(draws_2d: np.ndarray, max_num: int) -> dict[int, float]:
    """Greedy max-coverage cu ponderare de recență (extragerile recente cântăresc
    mai mult). Favorizează numere care acoperă extrageri DIVERSE recente."""
    n = draws_2d.shape[0]
    if n < 10:
        return {}
    B = _binary(draws_2d, max_num)
    draw_w = np.exp(np.linspace(-2.0, 0.0, n))
    scores = _greedy_cover(B, draw_w)
    return _normalize({i + 1: float(scores[i]) for i in range(max_num)}, max_num)


def score_cover_rarity(draws_2d: np.ndarray, max_num: int) -> dict[int, float]:
    """Greedy cover unde fiecare extragere e ponderată cu RARITATEA ei (inversul
    frecvenței globale a numerelor sale) × recență → acoperă combinațiile rare."""
    n = draws_2d.shape[0]
    if n < 10:
        return {}
    B = _binary(draws_2d, max_num)
    freq = B.sum(axis=1) + 1.0
    inv = 1.0 / freq
    draw_rarity = (B * inv[:, None]).sum(axis=0)
    rec = np.exp(np.linspace(-2.0, 0.0, n))
    draw_w = draw_rarity * rec
    if draw_w.sum() <= 0:
        draw_w = rec
    scores = _greedy_cover(B, draw_w)
    return _normalize({i + 1: float(scores[i]) for i in range(max_num)}, max_num)


def score_winslips(draws_2d: np.ndarray, max_num: int) -> dict[int, float]:
    """Stil WinSlips: acoperire roată abreviată pe PERECHI (covering design t=2).
    Scorăm numerele după contribuția la acoperirea perechilor frecvente din istoric."""
    n = draws_2d.shape[0]
    if n < 10:
        return {}
    B = _binary(draws_2d, max_num)
    rec = np.exp(np.linspace(-2.0, 0.0, n))
    Bw = B * rec[None, :]
    P = Bw @ B.T
    np.fill_diagonal(P, 0.0)
    Sall = P.sum(axis=1)
    a1 = 0.6
    sel = np.zeros(max_num, dtype=bool)
    Ssel = np.zeros(max_num, dtype=np.float64)
    scores = np.zeros(max_num, dtype=np.float64)
    for _step in range(max_num):
        gain = a1 * (Sall - Ssel) + (1.0 - a1) * Ssel
        gain[sel] = -np.inf
        idx = int(np.argmax(gain))
        if not np.isfinite(gain[idx]):
            break
        scores[idx] = max(gain[idx], 0.0)
        sel[idx] = True
        Ssel = Ssel + P[:, idx]
    return _normalize({i + 1: float(scores[i]) for i in range(max_num)}, max_num)


# ===========================================================================
# Metode noi de cover design (10)
# ===========================================================================

def score_cover_entropy_max(draws_2d: np.ndarray, max_num: int) -> dict[int, float]:
    """Maximizare entropie informațională: preferă numere cu auto-informație mare
    (rare în distribuție) × acoperire recentă → pool mai divers informațional."""
    n = draws_2d.shape[0]
    if n < 10:
        return {}
    B = _binary(draws_2d, max_num)
    rec = np.exp(np.linspace(-2.0, 0.0, n))
    freq_w = (B * rec[None, :]).sum(axis=1) + 1e-9
    p = freq_w / freq_w.sum()
    self_info = -np.log2(p)
    scores = {i + 1: float(self_info[i] * freq_w[i]) for i in range(max_num)}
    return _normalize(scores, max_num)


def score_cover_balanced_spread(draws_2d: np.ndarray, max_num: int) -> dict[int, float]:
    """Acoperire echilibrată: scor maxim pentru numere la marginile intervalului
    (distanță față de centru) × frecvență recentă → pool distribuit uniform 1..max_num."""
    n = draws_2d.shape[0]
    if n < 10:
        return {}
    B = _binary(draws_2d, max_num)
    rec = np.exp(np.linspace(-2.0, 0.0, n))
    freq_w = (B * rec[None, :]).sum(axis=1) + 1e-9
    center = (max_num + 1) / 2.0
    dist = np.abs(np.arange(1, max_num + 1) - center) / center
    scores = {i + 1: float(freq_w[i] * (0.5 + dist[i])) for i in range(max_num)}
    return _normalize(scores, max_num)


def score_cover_diversity_mmr(draws_2d: np.ndarray, max_num: int) -> dict[int, float]:
    """Maximum Marginal Relevance (MMR): echilibrează frecvența recentă cu
    DIVERSITATEA față de numerele deja selectate. λ=0.5 → 50% relevance, 50% diversity.
    Produce un set de numere cu co-apariții minime între ele."""
    n = draws_2d.shape[0]
    if n < 10:
        return {}
    B = _binary(draws_2d, max_num)
    rec = np.exp(np.linspace(-2.0, 0.0, n))
    freq_w = (B * rec[None, :]).sum(axis=1)
    freq_norm = freq_w / (freq_w.max() + 1e-9)
    # co-apariție normalizată (similaritate cosinus)
    Bn = B / (np.linalg.norm(B, axis=1, keepdims=True) + 1e-9)
    sim = Bn @ Bn.T  # (max_num, max_num)
    lam = 0.5
    selected = []
    remaining = list(range(max_num))
    scores = np.zeros(max_num)
    for step in range(max_num):
        if not remaining:
            break
        if not selected:
            idx = int(np.argmax(freq_norm[remaining]))
            best = remaining[idx]
        else:
            mmr = np.array([
                lam * freq_norm[j] - (1.0 - lam) * max(sim[j, s] for s in selected)
                for j in remaining
            ])
            best = remaining[int(np.argmax(mmr))]
        scores[best] = max(freq_w[best], 0.0)
        selected.append(best)
        remaining.remove(best)
    return _normalize({i + 1: float(scores[i]) for i in range(max_num)}, max_num)


def score_cover_harmonic_rank(draws_2d: np.ndarray, max_num: int) -> dict[int, float]:
    """Ponderare harmonică pe rangul de frecvență: numărul cu rangul k primește
    scorul 1/k. Amortizează dominanța numerelor cu frecvență mare → preferă
    un eșalonament lin al importanței."""
    n = draws_2d.shape[0]
    if n < 10:
        return {}
    B = _binary(draws_2d, max_num)
    rec = np.exp(np.linspace(-2.0, 0.0, n))
    freq_w = (B * rec[None, :]).sum(axis=1)
    ranks = np.argsort(np.argsort(-freq_w)) + 1  # rang 1 = cel mai frecvent
    scores = {i + 1: float(1.0 / ranks[i]) for i in range(max_num)}
    return _normalize(scores, max_num)


def score_cover_min_overlap(draws_2d: np.ndarray, max_num: int) -> dict[int, float]:
    """Minimizare suprapunere: favorează numere cu co-apariție MICĂ față de
    numerele cu frecvență mare. Produce un pool unde numerele sunt cât mai
    independente unele de altele (redundanță minimă)."""
    n = draws_2d.shape[0]
    if n < 10:
        return {}
    B = _binary(draws_2d, max_num)
    rec = np.exp(np.linspace(-2.0, 0.0, n))
    freq_w = (B * rec[None, :]).sum(axis=1)
    # co-apariție ponderată medie cu celelalte numere
    Bw = B * rec[None, :]
    cooc = (Bw @ B.T)  # (max_num, max_num)
    np.fill_diagonal(cooc, 0.0)
    # sum co-apariție cu TOP 20% numere (cele mai frecvente)
    top_k = max(1, max_num // 5)
    top_idx = np.argsort(-freq_w)[:top_k]
    overlap_with_top = cooc[:, top_idx].sum(axis=1)
    # scor final: frecvență × (1 - overlap normalizat)
    ov_norm = overlap_with_top / (overlap_with_top.max() + 1e-9)
    scores = {i + 1: float(freq_w[i] * (1.0 - 0.7 * ov_norm[i])) for i in range(max_num)}
    return _normalize(scores, max_num)


def score_cover_triplet(draws_2d: np.ndarray, max_num: int) -> dict[int, float]:
    """Covering design pe TRIPLETE (t=3): extinde winslips de la perechi la triplete.
    Scorăm fiecare număr după contribuția la acoperirea tripletelor frecvente din
    istoric — un pool ce acoperă mai multe triplete garantează mai ușor 4/5/6 corecte."""
    n = draws_2d.shape[0]
    if n < 10:
        return {}
    B = _binary(draws_2d, max_num)
    rec = np.exp(np.linspace(-2.0, 0.0, n))
    freq_w = (B * rec[None, :]).sum(axis=1)
    Bw = B * rec[None, :]
    # co-apariție perechi
    P = Bw @ B.T  # (max_num, max_num)
    np.fill_diagonal(P, 0.0)
    # triplet score(i) = sum over j≠i of freq(j) * P[i,j] → câte triplete conține i
    triplet_score = P @ freq_w  # (max_num,) — numărul i contribuie la triplete cu j și k
    scores = {i + 1: float(triplet_score[i]) for i in range(max_num)}
    return _normalize(scores, max_num)


def score_cover_temporal_shift(draws_2d: np.ndarray, max_num: int) -> dict[int, float]:
    """Detecție schimbare temporală: raportul frecvență recentă (25%) / frecvență istorică.
    Numere cu creștere recentă semnificativă primesc scor mare → pool adaptat la
    tendința curentă, nu la media istorică."""
    n = draws_2d.shape[0]
    if n < 10:
        return {}
    B = _binary(draws_2d, max_num)
    cut = max(1, n // 4)
    recent = B[:, -cut:].sum(axis=1) / cut
    historic = B[:, :-cut].sum(axis=1) / max(n - cut, 1)
    ratio = (recent + 1e-6) / (historic + 1e-6)
    scores = {i + 1: float(ratio[i] * (recent[i] + 0.1)) for i in range(max_num)}
    return _normalize(scores, max_num)


def score_cover_positional_bands(draws_2d: np.ndarray, max_num: int) -> dict[int, float]:
    """Acoperire pe benzi poziționale (decade): împarte [1..max_num] în 5 benzi egale
    și echilibrează selecția între ele. Numere din benzi sub-reprezentate în ponderile
    recente primesc un bonus → pool echilibrat pe tot intervalul."""
    n = draws_2d.shape[0]
    if n < 10:
        return {}
    B = _binary(draws_2d, max_num)
    rec = np.exp(np.linspace(-2.0, 0.0, n))
    freq_w = (B * rec[None, :]).sum(axis=1)
    n_bands = 5
    band_size = max_num / n_bands
    band_ids = (np.arange(max_num) / band_size).astype(int).clip(0, n_bands - 1)
    band_total = np.array([freq_w[band_ids == b].sum() + 1e-9 for b in range(n_bands)])
    band_avg = band_total / np.array([(band_ids == b).sum() + 1e-9 for b in range(n_bands)])
    band_inv = 1.0 / band_avg
    band_weight = band_inv[band_ids]  # per-number weight based on band scarcity
    scores = {i + 1: float(freq_w[i] * band_weight[i]) for i in range(max_num)}
    return _normalize(scores, max_num)


def score_cover_complement(draws_2d: np.ndarray, max_num: int) -> dict[int, float]:
    """Acoperire complement: identifică numerele ce rar co-apar cu TOP-K numere
    (frecvente). Aceste numere „complementare" completează pool-ul cu zone ale
    spațiului numeric neacoperite de candidații principali."""
    n = draws_2d.shape[0]
    if n < 10:
        return {}
    B = _binary(draws_2d, max_num)
    rec = np.exp(np.linspace(-2.0, 0.0, n))
    Bw = B * rec[None, :]
    freq_w = Bw.sum(axis=1)
    top_k = max(1, max_num // 4)
    top_idx = set(np.argsort(-freq_w)[:top_k].tolist())
    cooc = Bw @ B.T
    np.fill_diagonal(cooc, 0.0)
    # complement score: frecvență × (1 - co-apariție normalizată cu top)
    top_list = list(top_idx)
    cooc_top = cooc[:, top_list].mean(axis=1) if top_list else np.zeros(max_num)
    cooc_norm = cooc_top / (cooc_top.max() + 1e-9)
    scores = {i + 1: float(freq_w[i] * (1.0 - cooc_norm[i]) + 0.1 * (1.0 - cooc_norm[i]))
              for i in range(max_num)}
    return _normalize(scores, max_num)


def score_cover_adaptive_blend(draws_2d: np.ndarray, max_num: int) -> dict[int, float]:
    """Blend adaptiv: combină semnalele de la cover_greedy, cover_entropy_max și
    cover_temporal_shift cu ponderi proporționale cu variabilitatea lor (high-variance
    signal → mai puțin trustworthy → greutate mai mică). Meta-scorer de acoperire."""
    n = draws_2d.shape[0]
    if n < 10:
        return {}

    def _to_arr(d: dict[int, float]) -> np.ndarray:
        return np.array([d.get(i + 1, 0.0) for i in range(max_num)])

    s1 = _to_arr(score_cover_greedy(draws_2d, max_num))
    s2 = _to_arr(score_cover_entropy_max(draws_2d, max_num))
    s3 = _to_arr(score_cover_temporal_shift(draws_2d, max_num))

    stds = np.array([s1.std() + 1e-9, s2.std() + 1e-9, s3.std() + 1e-9])
    weights = 1.0 / stds
    weights /= weights.sum()
    blend = weights[0] * s1 + weights[1] * s2 + weights[2] * s3
    return _normalize({i + 1: float(blend[i]) for i in range(max_num)}, max_num)


# ===========================================================================
# Registry
# ===========================================================================
COVERAGE_METHODS: dict[str, tuple[Callable, str, bool, str]] = {
    # originale
    "cover_greedy": (score_cover_greedy, "coverage", False,
                     "greedy set-cover submodular (recență) — acoperire diversă · CPU"),
    "cover_rarity": (score_cover_rarity, "coverage", False,
                     "greedy cover ponderat pe raritatea extragerilor — combinații rare · CPU"),
    "winslips": (score_winslips, "coverage", False,
                 "stil WinSlips: acoperire roată abreviată pe perechi (covering design t=2) · CPU"),
    # noi
    "cover_entropy_max": (score_cover_entropy_max, "coverage", False,
                          "maximizare entropie informațională — pool divers informațional · CPU"),
    "cover_balanced_spread": (score_cover_balanced_spread, "coverage", False,
                              "spread echilibrat 1..max_num — margini + frecvență recentă · CPU"),
    "cover_diversity_mmr": (score_cover_diversity_mmr, "coverage", False,
                            "MMR: echilibru relevance/diversity — co-apariții minime · CPU"),
    "cover_harmonic_rank": (score_cover_harmonic_rank, "coverage", False,
                            "ponderare harmonică 1/k pe rang frecvență — eșalonament lin · CPU"),
    "cover_min_overlap": (score_cover_min_overlap, "coverage", False,
                          "minimizare suprapunere cu top-20% frecvente — redundanță mică · CPU"),
    "cover_triplet": (score_cover_triplet, "coverage", False,
                      "covering design t=3 (triplete) — extensie winslips la tripluri · CPU"),
    "cover_temporal_shift": (score_cover_temporal_shift, "coverage", False,
                             "detecție shift temporal 25%/75% — adaptare la tendința recentă · CPU"),
    "cover_positional_bands": (score_cover_positional_bands, "coverage", False,
                               "acoperire benzi poziționale (decade) — echilibru pe tot intervalul · CPU"),
    "cover_complement": (score_cover_complement, "coverage", False,
                         "numere complement față de top-25% — zone neacoperite de candidați · CPU"),
    "cover_adaptive_blend": (score_cover_adaptive_blend, "coverage", False,
                             "blend adaptiv greedy+entropie+shift (ponderi inverse-std) · CPU"),
}
