"""Metode de scoring bazate pe recență, goluri și serii de timp (16 metode).

Fiecare metodă răspunde la o ipoteză distinctă despre seria-indicator a unui
număr (1 = a ieșit la extragerea t):

    freq_window_50 / freq_window_200   numere „calde" pe fereastră scurtă / lungă
    ewma_hl30 / ewma_cold_hl30         căldură netezită exponențial / inversul ei
    gap_current / gap_ratio            „datorate": golul curent, brut / relativ la ritmul propriu
    gap_hazard                         hazard empiric: P(iese acum | a stat atât)
    momentum_30_300                    accelerare: frecvența scurtă minus cea lungă
    cusum_burst                        explozie recentă, indiferent de lungimea ferestrei
    holt_forecast                      nivel + tendință (Holt) pe seria-indicator
    autocorr_lag                       ponderi de autocorelație pe ultimele 20 de laguri
    ar_ls_pooled                       AR(10) comun tuturor numerelor, estimat prin CMMP
    spectral_phase                     componenta periodică dominantă, proiectată la pasul următor
    hurst_persistence                  exponentul Hurst (persistență) înmulțit cu deviația recentă
    hot_consistency                    în câte blocuri de 30 a fost peste așteptare
    rhythm_phase                       cât de aproape e golul curent de golul tipic propriu

Toate sunt deterministe, pure numpy, fără stare între apeluri și nu modifică
istoricul primit. Niciuna nu e prezentată ca avantaj statistic: bench-ul decide.
"""

from __future__ import annotations

import numpy as np

from .methods_common import (
    ewma_weights,
    expected_rate,
    indicator,
    last_seen_index,
    make_registry,
    vector_to_scores,
)

_EPS = 1e-9


# --------------------------------------------------------------------------- #
# Frecvență pe ferestre și netezire
# --------------------------------------------------------------------------- #
def _window_mean(ind: np.ndarray, window: int) -> np.ndarray:
    if ind.shape[0] == 0:
        return np.zeros(ind.shape[1])
    return ind[-int(window) :].mean(axis=0)


def score_freq_window_50(draws_2d, max_num):
    return vector_to_scores(_window_mean(indicator(draws_2d, max_num), 50), max_num)


def score_freq_window_200(draws_2d, max_num):
    return vector_to_scores(_window_mean(indicator(draws_2d, max_num), 200), max_num)


def _ewma(ind: np.ndarray, half_life: float) -> np.ndarray:
    n = ind.shape[0]
    if n == 0:
        return np.zeros(ind.shape[1])
    return ewma_weights(n, half_life) @ ind


def score_ewma_hl30(draws_2d, max_num):
    return vector_to_scores(_ewma(indicator(draws_2d, max_num), 30.0), max_num)


def score_ewma_cold_hl30(draws_2d, max_num):
    return vector_to_scores(-_ewma(indicator(draws_2d, max_num), 30.0), max_num)


# --------------------------------------------------------------------------- #
# Goluri
# --------------------------------------------------------------------------- #
def _current_gap(ind: np.ndarray) -> np.ndarray:
    """Extrageri scurse de la ultima apariție (niciodată văzut → n)."""
    n = ind.shape[0]
    last = last_seen_index(ind)
    return np.where(last < 0, float(n), (n - 1 - last).astype(np.float64))


def score_gap_current(draws_2d, max_num):
    return vector_to_scores(_current_gap(indicator(draws_2d, max_num)), max_num)


def _own_gaps(ind: np.ndarray, j: int) -> np.ndarray:
    idx = np.flatnonzero(ind[:, j] > 0)
    return np.diff(idx).astype(np.float64) if idx.size >= 2 else np.zeros(0)


def score_gap_ratio(draws_2d, max_num):
    ind = indicator(draws_2d, max_num)
    p0 = expected_rate(draws_2d, max_num)
    expected_gap = 1.0 / max(p0, _EPS)
    cur = _current_gap(ind)
    out = np.zeros(ind.shape[1])
    for j in range(ind.shape[1]):
        gaps = _own_gaps(ind, j)
        mean_gap = float(gaps.mean()) if gaps.size else expected_gap
        out[j] = (cur[j] + 1.0) / max(mean_gap, 1.0)
    return vector_to_scores(out, max_num)


def score_gap_hazard(draws_2d, max_num):
    """Hazard discret: P(golul se închide la pasul următor | a durat c pași).

    Estimat din golurile proprii ale numărului, netezit cu hazardul comun
    tuturor numerelor (α = 5 pseudo-observații), ca golurile rare să nu dea
    0/0. Fără istoric propriu: hazardul comun.
    """
    ind = indicator(draws_2d, max_num)
    n, m = ind.shape
    cur = _current_gap(ind) + 1.0  # golul care s-ar închide la pasul următor
    all_gaps = [_own_gaps(ind, j) for j in range(m)]
    nonempty = [g for g in all_gaps if g.size]
    pooled = np.concatenate(nonempty) if nonempty else np.zeros(0)
    alpha = 5.0
    out = np.zeros(m)
    for j in range(m):
        c = cur[j]
        gaps = all_gaps[j]
        if pooled.size:
            at_risk_pool = float((pooled >= c).sum())
            h_pool = float((pooled == c).sum()) / at_risk_pool if at_risk_pool else 0.0
        else:
            h_pool = expected_rate(draws_2d, max_num)
        if gaps.size:
            k_eq = float((gaps == c).sum())
            k_ge = float((gaps >= c).sum())
        else:
            k_eq = k_ge = 0.0
        out[j] = (k_eq + alpha * h_pool) / (k_ge + alpha)
    return vector_to_scores(out, max_num)


def score_rhythm_phase(draws_2d, max_num):
    """Cât de aproape e golul curent de golul tipic al numărului (în deviații)."""
    ind = indicator(draws_2d, max_num)
    m = ind.shape[1]
    cur = _current_gap(ind) + 1.0
    gaps_all = [_own_gaps(ind, j) for j in range(m)]
    nonempty = [g for g in gaps_all if g.size]
    pooled = np.concatenate(nonempty) if nonempty else np.zeros(0)
    mu_pool = float(pooled.mean()) if pooled.size else 1.0 / max(expected_rate(draws_2d, max_num), _EPS)
    sd_pool = float(pooled.std()) if pooled.size > 1 else max(mu_pool, 1.0)
    out = np.zeros(m)
    for j in range(m):
        gaps = gaps_all[j]
        if gaps.size >= 3:
            mu, sd = float(gaps.mean()), max(float(gaps.std()), 1.0)
        else:
            mu, sd = mu_pool, max(sd_pool, 1.0)
        out[j] = -abs(cur[j] - mu) / sd
    return vector_to_scores(out, max_num)


# --------------------------------------------------------------------------- #
# Momentum, CUSUM, consistență
# --------------------------------------------------------------------------- #
def score_momentum_30_300(draws_2d, max_num):
    ind = indicator(draws_2d, max_num)
    return vector_to_scores(_window_mean(ind, 30) - _window_mean(ind, 300), max_num)


def score_cusum_burst(draws_2d, max_num):
    """Max, peste k = 5..100, al excesului standardizat din ultimele k extrageri."""
    ind = indicator(draws_2d, max_num)
    n = ind.shape[0]
    if n == 0:
        return vector_to_scores(np.zeros(ind.shape[1]), max_num)
    p0 = expected_rate(draws_2d, max_num)
    k_max = min(100, n)
    rev = ind[::-1][:k_max]
    cs = np.cumsum(rev - p0, axis=0)
    k = np.arange(1, k_max + 1, dtype=np.float64)[:, None]
    z = cs / np.sqrt(k * max(p0 * (1.0 - p0), _EPS))
    start = min(4, k_max - 1)
    return vector_to_scores(z[start:].max(axis=0), max_num)


def score_hot_consistency(draws_2d, max_num):
    """În câte din ultimele 10 blocuri de 30 numărul a depășit așteptarea (+ tie-break pe frecvență)."""
    ind = indicator(draws_2d, max_num)
    n = ind.shape[0]
    p0 = expected_rate(draws_2d, max_num)
    block = 30
    n_blocks = min(10, n // block)
    if n_blocks == 0:
        return vector_to_scores(_window_mean(ind, 30), max_num)
    tail = ind[-n_blocks * block :].reshape(n_blocks, block, ind.shape[1])
    counts = tail.sum(axis=1)
    above = (counts > block * p0).sum(axis=0).astype(np.float64)
    freq = tail.reshape(-1, ind.shape[1]).mean(axis=0)
    return vector_to_scores(above + 0.01 * freq, max_num)


# --------------------------------------------------------------------------- #
# Serii de timp
# --------------------------------------------------------------------------- #
def score_holt_forecast(draws_2d, max_num, alpha: float = 0.05, beta: float = 0.01):
    """Holt (nivel + tendință) pe ultimele 400 de extrageri; scor = nivel + tendință."""
    ind = indicator(draws_2d, max_num)
    n, m = ind.shape
    if n == 0:
        return vector_to_scores(np.zeros(m), max_num)
    x = ind[-400:]
    warm = min(20, x.shape[0])
    level = x[:warm].mean(axis=0)
    trend = np.zeros(m)
    for t in range(warm, x.shape[0]):
        prev_level = level
        level = alpha * x[t] + (1.0 - alpha) * (level + trend)
        trend = beta * (level - prev_level) + (1.0 - beta) * trend
    return vector_to_scores(level + trend, max_num)


def score_autocorr_lag(draws_2d, max_num, lags: int = 20, window: int = 500):
    """Σ_l ρ_l · x_{n−l}: prognoză din autocorelațiile proprii pe ultimele 500 de extrageri."""
    ind = indicator(draws_2d, max_num)
    n, m = ind.shape
    x = ind[-window:]
    w = x.shape[0]
    if w < lags + 5:
        return vector_to_scores(_window_mean(ind, 50), max_num)
    x = x - x.mean(axis=0)
    denom = (x * x).sum(axis=0) + _EPS
    forecast = np.zeros(m)
    for lag in range(1, lags + 1):
        rho = (x[lag:] * x[:-lag]).sum(axis=0) / denom
        forecast += rho * x[w - lag]
    return vector_to_scores(forecast, max_num)


def score_ar_ls_pooled(draws_2d, max_num, order: int = 10, window: int = 400):
    """AR(10) cu coeficienți comuni tuturor numerelor (CMMP pe ultimele 400 de extrageri)."""
    ind = indicator(draws_2d, max_num)
    n, m = ind.shape
    x = ind[-window:]
    w = x.shape[0]
    if w <= order + 5:
        return vector_to_scores(_window_mean(ind, 50), max_num)
    # rânduri: (t, număr); coloane: I[t-1..t-order]; țintă: I[t]
    cols = [x[order - k - 1 : w - k - 1] for k in range(order)]
    X = np.stack(cols, axis=-1).reshape(-1, order)
    y = x[order:].reshape(-1)
    X = np.hstack([X, np.ones((X.shape[0], 1))])
    beta = np.linalg.solve(X.T @ X + 1e-3 * np.eye(order + 1), X.T @ y)
    last = np.stack([x[w - k - 1] for k in range(order)], axis=-1)
    last = np.hstack([last, np.ones((m, 1))])
    return vector_to_scores(last @ beta, max_num)


def score_spectral_phase(draws_2d, max_num, window: int = 256):
    """Componenta spectrală dominantă (perioadă 3..64) proiectată la extragerea următoare."""
    ind = indicator(draws_2d, max_num)
    n, m = ind.shape
    x = ind[-window:]
    w = x.shape[0]
    if w < 16:
        return vector_to_scores(_window_mean(ind, 50), max_num)
    x = x - x.mean(axis=0)
    spec = np.fft.rfft(x, axis=0)
    k = np.arange(spec.shape[0], dtype=np.float64)
    period = np.where(k > 0, w / np.maximum(k, 1.0), np.inf)
    allowed = (period >= 3.0) & (period <= 64.0)
    amp = np.abs(spec)
    amp[~allowed] = -1.0
    k_best = amp.argmax(axis=0)
    chosen = spec[k_best, np.arange(m)]
    # x_t ≈ (2/w)·Re(X_k·e^{2πi k t/w}); la t = w exponentul e 1 → Re(X_k).
    return vector_to_scores((2.0 / w) * chosen.real, max_num)


def score_hurst_persistence(draws_2d, max_num, window: int = 512):
    """Persistență (Hurst prin varianța agregată) × semnul deviației recente.

    H > 0,5: seria-indicator e persistentă → o deviație recentă pozitivă
    continuă; H < 0,5: anti-persistentă → deviația se inversează. Scorul
    e (H − 0,5)·(f_30 − p0): mare când persistența și căldura recentă concordă.
    """
    ind = indicator(draws_2d, max_num)
    n, m = ind.shape
    x = ind[-window:]
    w = x.shape[0]
    if w < 64:
        return vector_to_scores(_window_mean(ind, 50), max_num)
    x = x - x.mean(axis=0)
    scales = [1, 2, 4, 8, 16]
    log_var = []
    for sc in scales:
        nb = w // sc
        blocks = x[: nb * sc].reshape(nb, sc, m).mean(axis=1)
        log_var.append(np.log(blocks.var(axis=0) + _EPS))
    log_var = np.stack(log_var)  # (5, m)
    log_m = np.log(np.array(scales, dtype=np.float64))[:, None]
    lm = log_m - log_m.mean()
    slope = (lm * (log_var - log_var.mean(axis=0))).sum(axis=0) / (lm**2).sum()
    hurst = np.clip(1.0 + slope / 2.0, 0.0, 1.0)  # Var(m) ~ m^(2H-2)
    p0 = expected_rate(draws_2d, max_num)
    recent = ind[-30:].mean(axis=0) - p0
    return vector_to_scores((hurst - 0.5) * recent, max_num)


RECENCY_METHODS = make_registry(
    [
        ("freq_window_50", score_freq_window_50, "recency", "frecvența pe ultimele 50 de extrageri"),
        ("freq_window_200", score_freq_window_200, "recency", "frecvența pe ultimele 200 de extrageri"),
        ("ewma_hl30", score_ewma_hl30, "recency", "frecvență ponderată exponențial, timp de înjumătățire 30"),
        ("ewma_cold_hl30", score_ewma_cold_hl30, "recency", "numere reci: inversul EWMA(30)"),
        ("gap_current", score_gap_current, "gap", "extrageri scurse de la ultima apariție"),
        ("gap_ratio", score_gap_ratio, "gap", "golul curent raportat la golul mediu propriu"),
        ("gap_hazard", score_gap_hazard, "gap", "hazard empiric al golului, netezit cu hazardul comun"),
        ("rhythm_phase", score_rhythm_phase, "gap", "apropierea golului curent de golul tipic propriu"),
        ("momentum_30_300", score_momentum_30_300, "recency", "frecvența pe 30 minus frecvența pe 300"),
        ("cusum_burst", score_cusum_burst, "timeseries", "excesul standardizat maxim pe k = 5..100"),
        ("hot_consistency", score_hot_consistency, "recency", "blocuri de 30 (din 10) peste așteptare"),
        ("holt_forecast", score_holt_forecast, "timeseries", "Holt nivel + tendință pe seria-indicator"),
        ("autocorr_lag", score_autocorr_lag, "timeseries", "prognoză din autocorelații, 20 de laguri"),
        ("ar_ls_pooled", score_ar_ls_pooled, "timeseries", "AR(10) comun, CMMP pe 400 de extrageri"),
        ("spectral_phase", score_spectral_phase, "timeseries", "componenta periodică dominantă, pasul următor"),
        ("hurst_persistence", score_hurst_persistence, "timeseries", "exponent Hurst × deviația recentă"),
    ]
)

__all__ = ["RECENCY_METHODS"]
