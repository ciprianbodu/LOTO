"""Traiectorii cauzale din banca istorică din 9 noiembrie, folosite în producție.

Convenția fiecărei traiectorii este cea a studiului fixat. Unele stări
amortizate (Holt și CUSUM) emit scorul înaintea actualizării cu t-1.
Sursa istorică a porții Weibull rămâne disponibilă pentru comparația numerică.
"""

from __future__ import annotations

import numpy as np

_EPS = 1e-12


def _rows_norm(p: np.ndarray) -> np.ndarray:
    """Normalizează fiecare rând; rândurile degenerate devin uniforme."""
    p = np.maximum(np.asarray(p, dtype=np.float64), 0.0)
    s = p.sum(axis=1, keepdims=True)
    v = p.shape[1]
    out = np.where(s > _EPS, p / np.maximum(s, _EPS), 1.0 / v)
    return out


def _empty_traj(n: int, v: int) -> np.ndarray:
    out = np.empty((n, v), dtype=np.float64)
    if n:
        out[0] = 1.0 / v  # primul rând: nicio zi de istorie
    return out


def traj_freq_laplace(ind: np.ndarray, a: float = 0.5) -> np.ndarray:
    """Frecvență globală cu netezire Laplace."""
    n, v = ind.shape
    out = _empty_traj(n, v)
    counts = np.zeros(v)
    for t in range(1, n):
        counts += ind[t - 1]
        out[t] = (counts + a) / (counts.sum() + a * v)
    return out


def traj_ewma(ind: np.ndarray, halflife: float, floor: float = 0.25) -> np.ndarray:
    """Medie exponentială cu half-life; podea pentru pornirea rece."""
    n, v = ind.shape
    out = _empty_traj(n, v)
    lam = float(2.0 ** (-1.0 / halflife))
    s = np.zeros(v)
    for t in range(1, n):
        s = lam * s + ind[t - 1]
        out[t] = _rows_norm((s + floor)[None, :])[0]
    return out


def traj_gap_hazard(ind: np.ndarray, power: float = 1.0) -> np.ndarray:
    """Scor de recență proporțional cu inversul distanței de la ultima apariție."""
    n, v = ind.shape
    out = _empty_traj(n, v)
    gap = np.zeros(v)
    for t in range(1, n):
        gap += 1.0
        gap[ind[t - 1] > 0] = 0.0
        out[t] = _rows_norm((1.0 / (gap + 1.0) ** power)[None, :])[0]
    return out


def traj_gap_zscore(ind: np.ndarray) -> np.ndarray:
    """Z-score al golului curent față de media/σ istorică a inter-arrivalelor."""
    n, v = ind.shape
    out = _empty_traj(n, v)
    gap = np.zeros(v)
    cnt = np.zeros(v)
    mean = np.zeros(v)
    m2 = np.zeros(v)
    for t in range(1, n):
        gap += 1.0
        hit = ind[t - 1] > 0
        for j in np.flatnonzero(hit):
            g = gap[j]
            cnt[j] += 1
            d = g - mean[j]
            mean[j] += d / cnt[j]
            m2[j] += d * (g - mean[j])
            gap[j] = 0.0
        sd = np.sqrt(np.where(cnt > 1, m2 / np.maximum(cnt - 1, 1), 1.0))
        z = np.where(cnt > 1, (gap - mean) / np.maximum(sd, _EPS), 0.0)
        out[t] = _rows_norm(np.maximum(z - z.min() + _EPS, _EPS)[None, :])[0]
    return out


def traj_weibull_hazard(ind: np.ndarray, kappa: float) -> np.ndarray:
    """Hazard Weibull al golului: h(g) = (κ/λ)(g/λ)^(κ−1), λ = media inter-arrivalelor."""
    n, v = ind.shape
    out = _empty_traj(n, v)
    gap = np.zeros(v)
    cnt = np.zeros(v)
    mean = np.zeros(v)
    for t in range(1, n):
        gap += 1.0
        hit = ind[t - 1] > 0
        lam = np.where(cnt > 0, mean, v)
        lam = np.maximum(lam, 1.0)
        h = (kappa / lam) * np.maximum(gap / lam, 1e-9) ** (kappa - 1.0)
        out[t] = _rows_norm(h[None, :])[0]
        for j in np.flatnonzero(hit):
            g = gap[j]
            cnt[j] += 1
            mean[j] += (g - mean[j]) / cnt[j]
            gap[j] = 0.0
    return out


def traj_season_phase(ind: np.ndarray, period: int) -> np.ndarray:
    """Fază periodică: frecvența pe faza curentă a ciclului (period zile)."""
    n, v = ind.shape
    out = _empty_traj(n, v)
    sums = np.zeros((period, v))
    cnts = np.zeros(period)
    for t in range(1, n):
        phase = t % period
        prev_phase = (t - 1) % period
        sums[prev_phase] += ind[t - 1]
        cnts[prev_phase] += 1.0
        row = (sums[phase] + 0.5) / (cnts[phase] + 0.5 * v)
        out[t] = row / row.sum()
    return out


def traj_holt(ind: np.ndarray, alpha: float, beta: float, phi: float) -> np.ndarray:
    """Holt cu trend amortizat φ: nivel + trend per număr, prognoză un pas."""
    n, v = ind.shape
    out = _empty_traj(n, v)
    level = np.full(v, 1.0 / v)
    trend = np.zeros(v)
    for t in range(1, n):
        out[t] = _rows_norm(np.maximum(level + phi * trend, 0.0)[None, :])[0]
        x = ind[t - 1]
        prev_level = level
        level = alpha * x + (1 - alpha) * (level + phi * trend)
        trend = beta * (level - prev_level) + (1 - beta) * phi * trend
    return out


def traj_cusum(ind: np.ndarray, thresh: float) -> np.ndarray:
    """CUSUM unilateral peste rata de bază: depășirea pragului crește scorul."""
    n, v = ind.shape
    out = _empty_traj(n, v)
    s = np.zeros(v)
    cnt = np.zeros(v)
    for t in range(1, n):
        rate = (cnt + 0.5) / max(t - 1 + 0.5 * v, 1.0)
        out[t] = _rows_norm((np.maximum(s, 0.0) + _EPS)[None, :])[0]
        s = np.maximum(0.0, s + ind[t - 1] - rate - 1.0 / thresh)
        cnt += ind[t - 1]
    return out


def traj_entropy_penalty(ind: np.ndarray, win: int) -> np.ndarray:
    """Frecvență recentă penalizată de entropia locală a seriei (regularitate)."""
    n, v = ind.shape
    out = _empty_traj(n, v)
    for t in range(1, n):
        lo = max(0, t - win)
        w = ind[lo:t]
        m = max(w.shape[0], 1)
        freq = w.sum(axis=0) / m
        # Entropia blocurilor de lungime 2 per număr (regularitate temporală)
        ent = np.zeros(v)
        if m >= 8:
            x = (w > 0).astype(np.int8)
            b = x[:-1] * 2 + x[1:]  # 0..3
            for code in range(4):
                c = (b == code).sum(axis=0) / (m - 1)
                ent -= np.where(c > 0, c * np.log2(np.maximum(c, 1e-12)), 0.0)
            ent /= 2.0
        score = (freq + 0.5 / m) * (1.0 + 0.5 * (1.0 - ent / 2.0))
        out[t] = _rows_norm(score[None, :])[0]
    return out
