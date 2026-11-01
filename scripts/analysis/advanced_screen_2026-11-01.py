"""Ecran 3 de metode, 2026-11-01: matematici avansate NETESTATE inca (ecranul 1 a
rulat 1.002 scoreri clasici/ML, ecranul hit5plus 42 statici, hit5plus_screen2 29
meta/cross-game). Protocol fixat inainte de rulare.

Intrebarea: exista metode matematice avansate (valuri, AR online, informatie
mutuala, motive/matrix-profile, clusterizare spectrala, centralitate, procese
punctuale cu refractie, transport optimal, benzi conforme, Kalman cu trend,
GARCH, dependenta de coada, FFT multi-varf, shrinkage ierarhic, autocorelatie,
hazard Weibull, HMM) care depasesc liderul clasamentului?

Date, celule, nul, selectie, confirmare, promovare: identic cu
hit5plus_screen_2026-11-01.py (Romania 6/49, 5/40, Joker Urna 1; pool (11,16) x
tinta (4+,5+); 200 zile warmup; 70% dezvoltare / 30% confirmare; nul pe N_NULL
istorii sintetice uniforme cu toata banca refacuta; lider = decizia din
folds.csv la tinta 4; McNemar + binomial + Holm + replicare externa; promovare
doar cu Holm < 0.05 pe ambele si p extern < 0.05). N_NULL = 60 (fixat aici,
inainte de rulare; ecranele anterioare au folosit 100/200).

Candidati noi (toate cauzale: S[d] vede numai zilele < d; toate echivariante la
permutarea etichetelor; CPU, deterministe, fara GPU, fara filtre structurale):
  A1 wavelet_burst   energie Haar pe scalele 2..16 in fereastra W (128/256),
                     scor = ponderea energiei fine (burst recent); +/- 
  A2 ar2_online      AR(2) per numar pe indicator, LS online cu uitare 0.995,
                     prognoza clampata in [0,1]; +/-
  A3 mi_lag          informatie mutuala intarziata I(x_t ; y_{t-lag}) agregata
                     peste y, fereastra L (200/500), lag (1,2,5); +/-
  A4 motif           matrix-profile per numar: cea mai asemanatoare fereastra
                     trecuta (w=10, stride 5) si rata de aparitie a numarului in
                     cele 10 zile de dupa motiv; +/-
  A5 heat_kernel     (I + a*M)^p @ indicatorul ultimei zile, M = co-aparitie
                     decazuta pe L=500, p (4/8) — difuzie pe graf; +/-
  A6 eigen_cent      centralitate vector propriu a grafului de co-aparitie
                     (L=500 / all); +/-
  A7 refractory      rata de baza * exp(-aparitii_recente/tau), tau (3/10), si
                     inversa (auto-excitare)
  A8 chi2_dev        abaterea (semnata/patrata) a frecventei recente fata de
                     uniforma, W (100/300) — W1 pe axa numerelor era filtru
                     structural neechivariant si a fost scos inainte de rulare
  A9 conformal_z     z rolling al ratei pe fereastra W (200/500); +/-
  A10 kalman_trend   Kalman local liniar (nivel+panta) per numar, Q (1e-4/1e-3),
                     R=0.1; prognoza = nivel+panta; +/-
  A11 garch_vol      GARCH(1,1) pe indicator per numar (omega .05, a .1, b .85);
                     scor = volatilitatea prognozata; +/-
  A12 tail_dep       coeficient empiric de dependenta in coada superioara
                     (P(ambele in decila superioara)) pe L=500, agregat; +/-
  A13 fft_top3       suma top-3 magnitudini spectrale cu extrapolare de faza,
                     W (256/512); +/-
  A14 shrink_beta    (count + s*p_global)/(W + s), s (10/50/200), W (200/500)
  A15 autocorr_cyc   autocorelatia maxima pe lag 2..30 (W=512) inmultita cu
                     alinierea de faza curenta; +/-
  A16 weibull_gap    hazard Weibull pe goluri per numar, forma k (0.5/1/2)
  A17 hmm2_regime    HMM cu 2 stari pe seria zilei (minimul extragerii), scor =
                     p_stare_inalta * freq200 + (1-p) * freqall; +/-

Check-uri: leak si echivarianța se verifica pe toate familiile (h5.leak_check /
h5.equivariance_check, cu banca aceasta). Niciun rezultat nu se scrie daca
istoricul se schimba in timpul rularii.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import binomtest

HERE = Path(__file__).resolve().parent
os.environ.setdefault("ADV_NULL", "60")
os.environ.setdefault("ADV_WORKERS", "16")
os.environ.setdefault("ADV_OUT", str(HERE / "advanced_screen_2026-11-01.json"))

_spec = importlib.util.spec_from_file_location(
    "hit5plus_screen_20261101", HERE / "hit5plus_screen_2026-11-01.py"
)
h5 = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = h5  # necesar pentru pickle in ProcessPoolExecutor
_spec.loader.exec_module(h5)
ps = h5.ps

ROOT = ps.ROOT
sys.path.insert(0, str(ROOT))

SEED = 20261101
N_NULL = int(os.environ["ADV_NULL"])
WORKERS = int(os.environ["ADV_WORKERS"])
OUT = Path(os.environ["ADV_OUT"])

GAMES = {
    "ro_649": {**h5.GAMES["ro_649"], "Ks": (11, 16), "targets": (4, 5)},
    "ro_540": {**h5.GAMES["ro_540"], "Ks": (11, 16), "targets": (4, 5)},
    "ro_joker1": {**h5.GAMES["ro_joker1"], "Ks": (11, 16), "targets": (4, 5)},
}
h5.GAMES = GAMES  # h5.screen / h5.cells / h5.leaders citesc GAMES din modulul h5
EXTERNAL = h5.EXTERNAL


# ------------------------------------------------------------------ helpers
def _cs(data):
    return ps.cumsum0(data.C)


def _wavelet_burst(Ib: np.ndarray, N: int, W: int) -> np.ndarray:
    """Ponderea energiei Haar fine (scale 2..8) in ultimele W zile, per numar."""
    n_days = Ib.shape[0]
    S = np.zeros((n_days, N))
    for d in range(W, n_days):
        x = Ib[d - W:d, :]  # (W, N)
        e_fine = np.zeros(N)
        e_tot = np.sum(x * x, axis=0)
        w = W
        while w >= 8:
            h = w // 2
            d_ = (x[: 2 * h: 2, :] - x[1 : 2 * h : 2, :]) / np.sqrt(2.0)
            if w <= 16:
                e_fine += np.sum(d_ * d_, axis=0)
            x = (x[: 2 * h: 2, :] + x[1 : 2 * h : 2, :]) / np.sqrt(2.0)
            w = h
        S[d] = e_fine / np.maximum(e_tot, 1e-12)
    return S


def _ar2_online(Ib: np.ndarray, N: int, forget: float = 0.995) -> np.ndarray:
    """AR(2) per numar, LS online cu uitare; S[d] = prognoza pentru ziua d."""
    n_days = Ib.shape[0]
    S = np.zeros((n_days, N))
    # stare per numar: [a1, a2], matrice 2x2, vector 2
    A = np.zeros((N, 2))
    M = np.tile(np.eye(2) * 100.0, (N, 1, 1))
    b = np.zeros((N, 2))
    x1 = np.zeros(N)
    x2 = np.zeros(N)
    for d in range(2, n_days):
        y = Ib[d - 1]
        X = np.stack([x1, x2], axis=1)  # (N,2)
        pred = np.einsum("ni,nij,nj->n", X, M, b) / np.maximum(
            np.einsum("ni,nij,nj->n", X, M, X), 1e-12
        )
        S[d] = np.clip(pred, 0.0, 1.0)
        k = X / (1.0 + np.einsum("ni,nij,nj->n", X, M, X))[:, None]
        M = (M - np.einsum("nij,nk,nl->njl", M, k, X)) / forget
        b = b + k * (y - pred)[:, None]
        x2 = x1
        x1 = y
    return S


def _mi_lag(Ib: np.ndarray, N: int, L: int, lag: int) -> np.ndarray:
    """MI intarziata agregata: pentru fiecare d, MI(I[x,d] ; I[y,d-lag]) peste
    y, estimata pe fereastra L de zile anterioare (inclusiv d-1)."""
    n_days = Ib.shape[0]
    S = np.zeros((n_days, N))
    for d in range(lag + 1, n_days):
        lo = max(0, d - L)
        A = Ib[lo:d, :]  # (w, N) curent
        B = Ib[lo - lag:d - lag, :] if lo - lag >= 0 else np.vstack(
            [np.zeros((lag - lo, N)), Ib[: max(0, d - lag), :]]
        )
        w = A.shape[0]
        if w < 10:
            continue
        pa = A.mean(axis=0)
        pb = B.mean(axis=0)
        pab = (A * B).mean(axis=0)  # doar pe pereche (x,x) — MI e per pereche x,y
        # MI agregata peste y: sum_y MI(I[x,d] ; I[y,d-lag]) — calculam matricea
        # de contingenta pe perechi (x,y) prin produsul matricial al indicatorilor.
        C11 = (A.T @ B) / w  # (N,N) P(x=1, y=1)
        C10 = pa[:, None] - C11
        C01 = pb[None, :] - C11
        C00 = 1.0 - pa[:, None] - pb[None, :] + C11
        with np.errstate(divide="ignore", invalid="ignore"):
            mi = (
                C11 * np.log(C11 / (pa[:, None] * pb[None, :]))
                + C10 * np.log(C10 / (pa[:, None] * (1 - pb[None, :])))
                + C01 * np.log(C01 / ((1 - pa[:, None]) * pb[None, :]))
                + C00 * np.log(C00 / ((1 - pa[:, None]) * (1 - pb[None, :])))
            )
        mi = np.where(np.isfinite(mi), mi, 0.0)
        np.fill_diagonal(mi, 0.0)
        S[d] = mi.sum(axis=1)
    return S


def _motif(Ib: np.ndarray, N: int, w: int = 10, after: int = 10, stride: int = 5) -> np.ndarray:
    """Matrix-profile: pentru fiecare zi d si numar x, cea mai apropiata
    fereastra trecuta (distanta euclidiana pe indicator, stride fix) si rata
    lui x in cele `after` zile de dupa acel motiv."""
    n_days = Ib.shape[0]
    S = np.zeros((n_days, N))
    for x in range(N):
        s = Ib[:, x]
        if n_days <= w + after + 1:
            continue
        win = np.lib.stride_tricks.sliding_window_view(s, w)  # (n-w+1, w)
        # distante intre ferestre: |a-b|^2 = |a|^2+|b|^2-2 a.b
        sq = (win * win).sum(axis=1)
        D = sq[:, None] + sq[None, :] - 2.0 * (win @ win.T)
        np.fill_diagonal(D, np.inf)
        for d in range(w + after, n_days):
            j = int(np.argmin(D[d - w, : max(1, d - w - after)]))
            j = min(j, d - w - after - 1) if d - w - after - 1 >= 0 else 0
            S[d, x] = s[j + w: j + w + after].mean()
    return S


def _heat_kernel(Ib: np.ndarray, N: int, L: int, a: float = 0.05, p: int = 8) -> np.ndarray:
    """Heat kernel pe graful de co-aparitie decazut: S[d] = (I + a*M)^p @
    indicatorul zilei d-1. Polinom in M, deci exact echivariant la permutarea
    etichetelor; matricea M se actualizeaza dupa scor (causal)."""
    n_days = Ib.shape[0]
    S = np.zeros((n_days, N))
    M = np.zeros((N, N))
    alpha = 1.0 / max(1, L)
    K = np.eye(N)
    for d in range(1, n_days):
        if d >= 2:
            S[d] = K @ Ib[d - 1]
        o = np.outer(Ib[d], Ib[d])
        np.fill_diagonal(o, 0.0)
        M = (1 - alpha) * M + alpha * o
        K = np.eye(N) + a * M
        for _ in range(p - 1):
            K = K @ (np.eye(N) + a * M)
    return S


def _eigen_centrality(Ib: np.ndarray, N: int, L: int | None) -> np.ndarray:
    from scipy.linalg import eigh

    n_days = Ib.shape[0]
    S = np.zeros((n_days, N))
    M = np.zeros((N, N))
    A = np.zeros(N)
    alpha = None if L is None else 1.0 / max(1, L)
    for d in range(1, n_days):
        if d >= 30 and (alpha is not None or d % 20 == 0):
            deg = A + 1e-9
            Dm = np.diag(1.0 / np.sqrt(deg))
            vals, vecs = eigh(Dm @ M @ Dm)
            v = np.abs(vecs[:, -1])
            S[d] = v / v.max()
        o = np.outer(Ib[d], Ib[d])
        np.fill_diagonal(o, 0.0)
        if alpha is None:
            M += o
            A += Ib[d]
        else:
            M = (1 - alpha) * M + alpha * o
            A = (1 - alpha) * A + alpha * Ib[d]
    return S


def _refractory(CS: np.ndarray, N: int, W: int, tau: float, sign: float) -> np.ndarray:
    """rata pe W * exp(-aparitii_recente/tau) (sign=-1) sau * exp(+...) (sign=+1)."""
    n_days = CS.shape[0] - 1
    idx = np.arange(n_days)
    cnt = CS[idx] - CS[np.maximum(0, idx - W)]
    recent = CS[idx] - CS[np.maximum(0, idx - 5)]
    base = cnt / W
    return sign * base * np.exp(-sign * recent / tau)


def _chi2_dev(CS: np.ndarray, N: int, W: int, mode: str) -> np.ndarray:
    """Abaterea patrata a frecventei recente de uniforma (contributia chi2 per
    numar), eventual cu semn. Echivarianta: functie doar de coloana proprie."""
    n_days = CS.shape[0] - 1
    idx = np.arange(n_days)
    cnt = CS[idx] - CS[np.maximum(0, idx - W)]  # (n, N)
    exp = W / N
    dev = cnt - exp
    if mode == "signed":
        return dev / np.sqrt(exp)
    return dev * dev / exp


def _conformal_z(CS: np.ndarray, N: int, W: int) -> np.ndarray:
    """z rolling al ratei zilnice (media pe W, std pe W)."""
    n_days = CS.shape[0] - 1
    idx = np.arange(n_days)
    cnt = CS[idx] - CS[np.maximum(0, idx - W)]
    rate = cnt / W
    # media si std mobile pe rate
    c1 = np.vstack([np.zeros((1, N)), np.cumsum(rate, axis=0)])
    c2 = np.vstack([np.zeros((1, N)), np.cumsum(rate * rate, axis=0)])
    n = np.minimum(idx, W)[:, None]
    mean = (c1[idx] - c1[np.maximum(0, idx - W)]) / np.maximum(n, 1)
    var = (c2[idx] - c2[np.maximum(0, idx - W)]) / np.maximum(n, 1) - mean**2
    return (rate - mean) / np.sqrt(np.maximum(var, 1e-12))


def _kalman_trend(Ib: np.ndarray, N: int, Q: float, R: float = 0.1) -> np.ndarray:
    """Kalman local liniar per numar: stare [nivel, panta]; S[d] = nivel+panta."""
    n_days = Ib.shape[0]
    S = np.zeros((n_days, N))
    lvl = np.zeros(N)
    slp = np.zeros(N)
    P = np.ones((N, 2, 2)) * 1.0
    F = np.array([[1.0, 1.0], [0.0, 1.0]])
    for d in range(1, n_days):
        # predict
        lvl_p = lvl + slp
        slp_p = slp
        P_p = np.einsum("ij,njk,lk->nil", F, P, F) + Q * np.eye(2)[None]
        # update
        y = Ib[d]
        H = np.array([1.0, 0.0])
        PHt = np.einsum("nij,j->ni", P_p, H)
        S[d] = np.clip(lvl_p + slp_p, 0.0, 1.0)
        innov = y - lvl_p
        K = PHt / (PHt[:, 0] + R)[:, None]
        lvl = lvl_p + K[:, 0] * innov
        slp = slp_p + K[:, 1] * innov
        P = P_p - np.einsum("ni,nj->nij", K, PHt)
    return S


def _garch_vol(Ib: np.ndarray, N: int, omega: float = 0.05, a: float = 0.1, b: float = 0.85) -> np.ndarray:
    """GARCH(1,1) pe indicator per numar; S[d] = sqrt(h_d) (volatilitate)."""
    n_days = Ib.shape[0]
    S = np.zeros((n_days, N))
    h = np.full(N, 0.05)
    for d in range(1, n_days):
        y = Ib[d - 1]
        h = omega + a * (y - 0.0) ** 2 + b * h
        S[d] = np.sqrt(h)
    return S


def _tail_dep(Ib: np.ndarray, N: int, L: int, q: float = 0.9) -> np.ndarray:
    """Dependenta in coada superioara: P(y in top-q | x in top-q) agregata."""
    n_days = Ib.shape[0]
    S = np.zeros((n_days, N))
    for d in range(L, n_days):
        A = Ib[d - L:d, :]
        thr = np.quantile(A, q, axis=0)
        top = A >= thr[None, :]
        px = top.mean(axis=0)
        both = (top.T @ top) / L
        with np.errstate(divide="ignore", invalid="ignore"):
            dep = np.where(px[None, :] > 0, both / np.maximum(px[None, :], 1e-12), 0.0)
        np.fill_diagonal(dep, 0.0)
        S[d] = dep.sum(axis=1)
    return S


def _fft_top3(Ib: np.ndarray, N: int, W: int, top: int = 3) -> np.ndarray:
    """FFT pe axa TIMP per numar (fereastra W de zile anterioare); scor = suma
    top-3 magnitudini cu extrapolare de faza la orizont 1. Echivariant: FFT
    doar pe coloana proprie."""
    n_days = Ib.shape[0]
    S = np.zeros((n_days, N))
    cols = np.arange(N)
    for d in range(W, n_days):
        X = Ib[d - W:d, :]  # (W, N), doar zile < d
        X = X - X.mean(axis=0, keepdims=True)
        F = np.fft.rfft(X, axis=0)  # (W/2+1, N)
        mag = np.abs(F)
        order = np.argsort(mag[1:], axis=0)[::-1][:top] + 1  # (top, N)
        rec = np.zeros(N)
        for r in range(top):
            k = order[r]  # (N,)
            rec += mag[k, cols] * np.cos(2 * np.pi * k * W / W + np.angle(F[k, cols]))
        S[d] = rec
    return S


def _shrink_beta(CS: np.ndarray, N: int, W: int, s: float) -> np.ndarray:
    n_days = CS.shape[0] - 1
    idx = np.arange(n_days)
    cnt = CS[idx] - CS[np.maximum(0, idx - W)]
    tot = cnt.sum(axis=1, keepdims=True)
    p = tot / (W * N)
    return (cnt + s * p) / (W + s)


def _autocorr_cycle(Ib: np.ndarray, N: int, W: int, maxlag: int = 30) -> np.ndarray:
    """Autocorelatia maxima pe lag 2..maxlag, inmultita cu alinierea de faza."""
    n_days = Ib.shape[0]
    S = np.zeros((n_days, N))
    for d in range(W, n_days):
        x = Ib[d - W:d, :]
        x = x - x.mean(axis=0, keepdims=True)
        var = (x * x).mean(axis=0)
        best = np.zeros(N)
        bestlag = np.full(N, 2.0)
        for lag in range(2, maxlag + 1):
            ac = (x[:-lag] * x[lag:]).mean(axis=0) / np.maximum(var, 1e-12)
            better = ac > best
            best = np.where(better, ac, best)
            bestlag = np.where(better, lag, bestlag)
        # aliniere de faza curenta cu cel mai bun lag
        S[d] = best * np.cos(2 * np.pi * (d % bestlag) / bestlag)
    return S


def _weibull_gap(Ib: np.ndarray, N: int, k: float, gmax: int = 100) -> np.ndarray:
    """Hazard Weibull pe golul curent al fiecarui numar."""
    n_days, N = Ib.shape
    G = ps.gaps(Ib)  # (n, N) golul pana la ziua d (causal)
    csum = np.vstack([np.zeros((1, N)), np.cumsum(G, axis=0)])
    idx = np.arange(n_days)
    lam = np.maximum(1.0, csum[idx] / np.maximum(idx, 1)[:, None])  # media pana la d
    g = np.minimum(G, gmax)
    return k / lam * (g / lam) ** (k - 1) * np.exp(-((g / lam) ** k))


def _hmm2_regime(data) -> np.ndarray:
    """HMM 2 stari pe log-verosimilitatea zilei sub modelul agregat (emisii
    gaussiene; ll este simetrica in etichete, deci echivarianta); scor =
    p(stare inalta) * freq200 + (1-p) * freqall, per numar."""
    N = data.N
    CS = _cs(data)
    n_days = CS.shape[0] - 1
    idx = np.arange(n_days)
    f200 = (CS[idx] - CS[np.maximum(0, idx - 200)]) / 200.0
    fall = CS[idx] / np.maximum(idx, 1)[:, None]
    S = np.zeros((n_days, N))
    ll = np.zeros(n_days)
    for d in range(1, n_days):
        # log-verosimilitatea zilei d-1 sub modelul agregat pana la d-1 (causal)
        p = (CS[d - 1] + 1.0) / (d - 1 + N)
        ll[d] = float((data.Ib[d - 1] * np.log(p)).sum())
    # parametrii de emisie din primele 200 de zile (causal: nu depind de viitor)
    mu = np.array([np.quantile(ll[50:200], 0.25), np.quantile(ll[50:200], 0.75)])
    sd = np.array([ll[50:200].std() * 0.5 + 1e-6, ll[50:200].std() * 0.5 + 1e-6])
    trans = np.array([[0.95, 0.05], [0.05, 0.95]])
    alpha = np.array([0.5, 0.5])
    for d in range(1, n_days):
        emit = np.exp(-0.5 * ((ll[d] - mu) / sd) ** 2) / sd
        a = alpha * emit
        alpha = a / a.sum()
        alpha = alpha @ trans
        alpha = alpha / alpha.sum()
        p_hi = alpha[1]
        S[d] = p_hi * f200[d] + (1 - p_hi) * fall[d]
    return S


# ------------------------------------------------------------------ banca
def bank3(data):
    """Generator (nume, familie, scor zile x numere); S[d] vede numai zilele < d."""
    N, Ib = data.N, data.Ib
    CS = _cs(data)

    def both(name, fam, S):
        yield name, fam, S
        yield f"anti_{name}", fam, -S

    for W in (128, 256):
        yield from both(f"wavelet_W{W}", "A1_wavelet", _wavelet_burst(Ib, N, W))
    yield from both("ar2_online", "A2_ar2", _ar2_online(Ib, N))
    for L in (200, 500):
        for lag in (1, 2, 5):
            yield from both(f"mi_L{L}_lag{lag}", "A3_mi", _mi_lag(Ib, N, L, lag))
    yield from both("motif_w10", "A4_motif", _motif(Ib, N))
    for p in (4, 8):
        yield from both(f"heat_p{p}", "A5_heatkernel", _heat_kernel(Ib, N, 500, 0.05, p))
    for L in (500, None):
        yield from both(f"eigen_L{'all' if L is None else L}", "A6_eigen", _eigen_centrality(Ib, N, L))
    for tau in (3.0, 10.0):
        yield from both(f"refract_tau{int(tau)}", "A7_refractory", _refractory(CS, N, 200, tau, -1.0))
        yield from both(f"selfexc_tau{int(tau)}", "A7_refractory", _refractory(CS, N, 200, tau, +1.0))
    for W in (100, 300):
        yield from both(f"chi2_signed_W{W}", "A8_chi2dev", _chi2_dev(CS, N, W, "signed"))
        yield from both(f"chi2_abs_W{W}", "A8_chi2dev", _chi2_dev(CS, N, W, "abs"))
    for W in (200, 500):
        yield from both(f"confz_W{W}", "A9_conformal", _conformal_z(CS, N, W))
    for Q in (1e-4, 1e-3):
        yield from both(f"kalman_Q{Q:g}", "A10_kalman", _kalman_trend(Ib, N, Q))
    yield from both("garch_vol", "A11_garch", _garch_vol(Ib, N))
    yield from both("taildep_L500", "A12_taildep", _tail_dep(Ib, N, 500))
    for W in (256, 512):
        yield from both(f"fft3_W{W}", "A13_fft", _fft_top3(Ib, N, W))
    for W in (200, 500):
        for s in (10.0, 50.0, 200.0):
            yield f"shrink_W{W}_s{int(s)}", "A14_shrink", _shrink_beta(CS, N, W, s)
    yield from both("autocorr_cyc", "A15_autocorr", _autocorr_cycle(Ib, N, 512))
    for k in (0.5, 1.0, 2.0):
        yield f"weibull_k{k}", "A16_weibull", _weibull_gap(Ib, N, k)
    yield from both("hmm2_regime", "A17_hmm", _hmm2_regime(data))


h5.bank = bank3  # h5.screen / h5.leak_check / h5.equivariance_check folosesc banca noua


def main() -> None:
    logging.disable(logging.WARNING)
    t0 = time.time()
    fp = h5.data_fingerprint()
    report: dict = {"protocol": __doc__, "data_sha256": fp, "n_null": N_NULL, "games": {}}
    games = sys.argv[1:] or list(GAMES)

    d649 = h5.load_game("ro_649")
    print(f"[leak] {h5.leak_check(d649)} scoreri fara date viitoare", flush=True)
    print(f"[equivariance] {h5.equivariance_check(d649)} scoreri verificati", flush=True)

    frozen: list[dict] = []
    with ProcessPoolExecutor(WORKERS) as pool:
        for game in games:
            data = h5.load_game(game)
            table, store, m = h5.screen(data, game, keep_hits=True)
            n_scorers = table["name"].nunique()
            print(f"[{game}] scoreri={n_scorers} teste={len(table)} gated={int(table['gated'].sum())} "
                  f"({time.time() - t0:.0f}s)", flush=True)
            obs = h5.cell_max(table)
            seeds = [(game, SEED + 1000 * (list(GAMES).index(game) + 1) + i) for i in range(N_NULL)]
            null = list(pool.map(h5._null_one, seeds))
            search = {}
            for key, val in obs.items():
                arr_e = np.array([nv.get(key, {}).get("z_event", -np.inf) for nv in null])
                arr_h = np.array([nv.get(key, {}).get("z_hits", -np.inf) for nv in null])
                search[key] = {
                    "max_dev_z_event": val["z_event"], "p_search_event": float((1 + (arr_e >= val["z_event"]).sum()) / (len(arr_e) + 1)),
                    "max_dev_z_hits": val["z_hits"], "p_search_hits": float((1 + (arr_h >= val["z_hits"]).sum()) / (len(arr_h) + 1)),
                }
            print(f"[{game}] nul {N_NULL} istorii ({time.time() - t0:.0f}s): "
                  + ", ".join(f"{k} p={v['p_search_event']:.2f}" for k, v in search.items()), flush=True)
            lead = h5.leaders(game)
            lead_hits, lead_fb = {}, {}
            for name in sorted(set(lead.values())):
                lead_hits[name], lead_fb[name] = h5.leader_hits(game, data, name, m["all"], pool)
            sel = h5.select(table, game)
            for s in sel:
                hits = store[(s["name"], s["K"])]
                lh = lead_hits[lead[s["K"]]][s["K"]]
                conf = m["conf"]
                new_ev = hits[conf] >= s["target"]
                lead_ev = lh[conf] >= s["target"]
                b, c, p_mc = h5.mcnemar_greater(new_ev, lead_ev)
                p0 = h5.tail(data.N, data.d, s["K"], s["target"])
                n = int(conf.sum())
                e = int(new_ev.sum())
                s.update({
                    "leader": lead[s["K"]],
                    "leader_dev_rate": float((lh[m["dev"]] >= s["target"]).mean()),
                    "conf_n": n, "conf_events": e, "conf_rate": e / n,
                    "conf_leader_events": int(lead_ev.sum()), "conf_leader_rate": float(lead_ev.mean()),
                    "conf_p0": p0, "mcnemar_b": b, "mcnemar_c": c, "p_vs_leader": p_mc,
                    "p_vs_random": float(binomtest(e, n, p0, alternative="greater").pvalue),
                })
                frozen.append(s)
            report["games"][game] = {
                "rows": int(len(data.draws)), "days": int(data.n_days), "scorers": int(n_scorers),
                "tests": int(len(table)), "gated": int(table["gated"].sum()),
                "families": table.groupby("family")["name"].nunique().to_dict(),
                "search": search,
                "leaders": {f"{K}": v for K, v in lead.items()},
                "leader_fallback_days": lead_fb,
                "leader_rates": {
                    f"{K}_{t}": {
                        "dev": float((lead_hits[v][K][m["dev"]] >= t).mean()),
                        "conf": float((lead_hits[v][K][m["conf"]] >= t).mean()),
                        "p0": h5.tail(data.N, data.d, K, t),
                    }
                    for K, v in lead.items() for t in GAMES[game]["targets"]
                },
            }
            if h5.data_fingerprint() != fp:
                raise SystemExit("istoricul s-a schimbat in timpul rularii; rezultatul nu se scrie")

        h5.external_tests(frozen, pool)
        print(f"[extern] {sum(1 for f in frozen if f['external'])} candidati ({time.time() - t0:.0f}s)", flush=True)

    adj_l = ps.holm([f["p_vs_leader"] for f in frozen])
    adj_r = ps.holm([f["p_vs_random"] for f in frozen])
    for f, al, ar in zip(frozen, adj_l, adj_r):
        f["holm_vs_leader"], f["holm_vs_random"] = al, ar
        ext_ok = f["external"] is None or f["external"]["p"] < 0.05
        f["promoted"] = bool(al < 0.05 and ar < 0.05 and ext_ok)
    report["frozen"] = frozen
    report["promoted"] = [f for f in frozen if f["promoted"]]
    report["runtime_s"] = time.time() - t0
    if h5.data_fingerprint() != fp:
        raise SystemExit("istoricul s-a schimbat in timpul rularii; rezultatul nu se scrie")
    OUT.write_text(json.dumps(report, indent=1, default=float), encoding="utf-8")
    print(f"[gata] {len(frozen)} inghetati, {len(report['promoted'])} promovati, {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
