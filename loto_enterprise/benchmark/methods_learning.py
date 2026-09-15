"""Metode de scoring cu învățare (4 metode), toate ieftine și deterministe.

Un singur model comun tuturor numerelor (pooled), pe trăsături calculate
strict din istoricul de dinaintea fiecărei extrageri de antrenare:

    ridge_pooled_feats     regresie ridge (formă închisă) pe 6 trăsături
    logit_pooled_feats     regresie logistică cu 8 pași IRLS, aceleași trăsături
    online_logit_sgd       aceeași regresie, învățată online, o trecere prin ultimele 400 de extrageri
    rank_ensemble_core     media rangurilor a cinci metode din familii diferite

Trăsături per (extragere t, număr j), toate „la închiderea" extragerii t−1:
    frecvența pe 50, EWMA(30), golul curent normalizat, momentul 30−300,
    indicatorul ultimei extrageri, termen liber.
"""

from __future__ import annotations

import numpy as np
from scipy.signal import lfilter

from .methods_common import expected_rate, indicator, make_registry, vector_to_scores

_EPS = 1e-9
_TRAIN_WINDOW = 400
_RIDGE = 1.0


def _features_from(ind: np.ndarray, p0: float, t_lo: int) -> np.ndarray:
    """Trăsături pentru t = t_lo..n (inclusiv n = extragerea de prezis); folosesc doar I[:t].

    Cumulările se fac o dată pe tot istoricul; rândurile de trăsături se
    construiesc numai pentru fereastra cerută (cost proporțional cu fereastra).
    """
    n, m = ind.shape
    t_lo = int(max(0, min(t_lo, n)))
    S = np.vstack([np.zeros((1, m)), np.cumsum(ind, axis=0)])  # S[t] = Σ_{s<t} I_s
    t_idx = np.arange(t_lo, n + 1, dtype=np.float64)
    t_int = t_idx.astype(np.int64)

    def win_mean(w: int) -> np.ndarray:
        lo = np.clip(t_int - w, 0, None)
        length = np.maximum(t_idx - lo, 1.0)
        return (S[t_int] - S[lo]) / length[:, None]

    f50 = win_mean(50)
    f30 = win_mean(30)
    f300 = win_mean(300)
    # EWMA(30) „închis" la t−1: e_incl[t] = λ e_incl[t−1] + (1−λ) I[t]; decalat cu 1
    lam = 0.5 ** (1.0 / 30.0)
    e_incl = lfilter([1.0 - lam], [1.0, -lam], ind, axis=0) if n else np.zeros((0, m))
    e = np.vstack([np.zeros((1, m)), e_incl])[t_int]
    # golul la t: t − 1 − ultima apariție < t (niciodată → t)
    seen = np.where(ind > 0, np.arange(n)[:, None], -1)
    last_upto = np.maximum.accumulate(seen, axis=0) if n else np.zeros((0, m), dtype=np.int64)
    last_before = np.vstack([np.full((1, m), -1), last_upto])[t_int]
    gap = np.where(last_before < 0, t_idx[:, None], t_idx[:, None] - 1 - last_before) * p0
    last_ind = np.vstack([np.zeros((1, m)), ind])[t_int]
    ones = np.ones((t_int.shape[0], m))
    return np.stack([f50, e, gap, f30 - f300, last_ind, ones], axis=-1)


def _train_rows(ind: np.ndarray, p0: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(X_train, y_train, X_next): ultimele `_TRAIN_WINDOW` extrageri ca antrenare."""
    n, m = ind.shape
    start = max(n - _TRAIN_WINDOW, 50)
    if start >= n:
        start = max(n - 20, 0)
    F = _features_from(ind, p0, start)  # (n - start + 1, m, 6)
    X = F[:-1].reshape(-1, F.shape[-1])
    y = ind[start:n].reshape(-1)
    return X, y, F[-1]


def score_ridge_pooled_feats(draws_2d, max_num):
    ind = indicator(draws_2d, max_num)
    n, m = ind.shape
    if n < 30:
        return vector_to_scores(ind.sum(axis=0), max_num)
    X, y, X_next = _train_rows(ind, expected_rate(draws_2d, max_num))
    A = X.T @ X + _RIDGE * np.eye(X.shape[1])
    beta = np.linalg.solve(A, X.T @ y)
    return vector_to_scores(X_next @ beta, max_num)


def _logit_fit(X: np.ndarray, y: np.ndarray, iters: int = 8) -> np.ndarray:
    beta = np.zeros(X.shape[1])
    for _ in range(iters):
        z = X @ beta
        p = 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))
        w = p * (1.0 - p) + 1e-6
        H = (X * w[:, None]).T @ X + _RIDGE * np.eye(X.shape[1])
        g = X.T @ (y - p) - _RIDGE * beta
        beta = beta + np.linalg.solve(H, g)
    return beta


def score_logit_pooled_feats(draws_2d, max_num):
    ind = indicator(draws_2d, max_num)
    n, m = ind.shape
    if n < 30:
        return vector_to_scores(ind.sum(axis=0), max_num)
    X, y, X_next = _train_rows(ind, expected_rate(draws_2d, max_num))
    beta = _logit_fit(X, y)
    return vector_to_scores(X_next @ beta, max_num)


def score_online_logit_sgd(draws_2d, max_num, lr: float = 0.05):
    """O trecere online, în ordine cronologică, cu pas fix; scorul = z la extragerea următoare."""
    ind = indicator(draws_2d, max_num)
    n, m = ind.shape
    if n < 30:
        return vector_to_scores(ind.sum(axis=0), max_num)
    start = max(n - _TRAIN_WINDOW, 50)
    if start >= n:
        start = max(n - 20, 0)
    F = _features_from(ind, expected_rate(draws_2d, max_num), start)
    beta = np.zeros(F.shape[-1])
    for k, t in enumerate(range(start, n)):
        Xt = F[k]
        p = 1.0 / (1.0 + np.exp(-np.clip(Xt @ beta, -30, 30)))
        beta += lr * (Xt.T @ (ind[t] - p)) / m
    return vector_to_scores(F[-1] @ beta, max_num)


def score_rank_ensemble_core(draws_2d, max_num):
    """Media rangurilor: freq_window_200, ewma_hl30, gap_hazard, markov_pairs, autocorr_lag."""
    from .methods_recency import (
        score_autocorr_lag,
        score_ewma_hl30,
        score_freq_window_200,
        score_gap_hazard,
    )
    from .methods_relational import score_markov_pairs

    members = (
        score_freq_window_200,
        score_ewma_hl30,
        score_gap_hazard,
        score_markov_pairs,
        score_autocorr_lag,
    )
    m = int(max_num)
    acc = np.zeros(m)
    for fn in members:
        s = fn(draws_2d, max_num)
        vec = np.array([s.get(i + 1, 0.0) for i in range(m)], dtype=np.float64)
        ranks = np.empty(m)
        ranks[np.argsort(vec, kind="stable")] = np.arange(m, dtype=np.float64)
        acc += ranks
    return vector_to_scores(acc / len(members), max_num)


LEARNING_METHODS = make_registry(
    [
        ("ridge_pooled_feats", score_ridge_pooled_feats, "learning", "ridge pe 6 trăsături, model comun"),
        ("logit_pooled_feats", score_logit_pooled_feats, "learning", "logistică IRLS pe 6 trăsături, model comun"),
        ("online_logit_sgd", score_online_logit_sgd, "learning", "logistică online, o trecere, pas fix"),
        ("rank_ensemble_core", score_rank_ensemble_core, "ensemble", "media rangurilor a 5 metode din familii diferite"),
    ]
)

__all__ = ["LEARNING_METHODS"]
