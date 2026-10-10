"""Ecran de ~1.000 de metode noi fata de liderul clasamentului, 2026-10-10.

Protocol fixat inainte de rulare.

Intrebarea utilizatorului: exista metode noi care depasesc clasamentul (liderul
bench pe fiecare joc si pool)? Cele care il depasesc intra in aplicatie.

Date. Romania: 6/49, 5/40 (sase extrase), Joker Urna 1, Joker Urna 2, ca in
prediction_screen_2026-10-05.py. Scorul unei zile vede numai zilele ANTERIOARE;
extragerile din aceeasi zi primesc acelasi scor. 200 de zile warmup, primele 70%
din rest dezvoltare, ultimele 30% confirmare.

Candidati. Familii noi, toate echivariante (scorul urmeaza numarul, nu
eticheta lui), fara filtre structurale, fara GPU:
  F01 frecventa cu nucleu putere l^-b (b 0.3..2, orizont 50..1000)
  F02 diferenta a doua EWMA (rapida minus lenta, 45 de perechi)
  F03 golul fiecarui numar fata de propriile goluri (percentila, z, raport la
      mediana, hazard propriu cu micsorare spre hazardul comun)
  F04 Markov pe decalaje 1..10 cu tranzitii pe fereastra (200, 500, 1000 de
      zile) si pe tot istoricul pentru decalajele netestate pe 2026-10-05
  F05 auto-tranzitie: P(numarul apare | starea lui acum k zile), k 1..10
  F06 co-aparitie cu numerele din ultimele w zile, pe fereastra L, fara
      termenul diagonal
  F07 co-aparitie cu starea EWMA
  F08 completarea perechilor numerelor fierbinti (toate egalitatile incluse)
  F09 PageRank pe graful de co-aparitie, pe fereastra
  F10 Holt pe fiecare numar (nivel + tendinta)
  F11 CUSUM pe fiecare numar, cu si fara uitare
  F12 panta regresiei pe ultimele W zile
  F13 regularitatea golurilor (coeficient de variatie) si golul ponderat cu ea
  F14 analog k-NN pe starea zilei (ultima zi, EWMA, fereastra 50)
  F15 Markov multi-decalaj cu ponderi descrescatoare
  F16 hazard comun pe fereastra
  F17 spectru propriu (perioada dominanta a fiecarui numar, extrapolata)
  F18 ansambluri pe rang: toate perechile si tripletele din 12 trasaturi de
      baza, plus 80 de cvadruple trase cu seed fix
  F19 regresie ridge online pe 25 de subseturi de 5 trasaturi (seed fix), x2 lambda
  Fiecare familie scalara apare si cu semn schimbat. La Urna 2 (o singura bila)
  familiile de co-aparitie nu exista.

Celule. Ca in clasamentul aplicatiei: 6/49 si Joker Urna 1 pe pool-urile
(6 sau 8, 11, 16) la tintele 3+ si 4+; 5/40 pe (8, 11, 16) la 4+; Urna 2 top-1.
Liderul celulei = castigatorul deciziei din bench_results/folds.csv la tinta
celulei (decision.decide_optimal_config_for_pool), rulat pe aceleasi extrageri
cu call_method, rank_by_score si rezerva `frequency` la scor inutilizabil, ca
in productie.

Selectie pe dezvoltare, per celula: candidati fara dependenta de egalitati
(locul K decis de numar in cel mult 50% din randuri) si stabili (exces de
hituri pe ambele jumatati ale dezvoltarii); primii 2 dupa z al evenimentului
tinta si primul dupa z al hiturilor (nume distincte). La Urna 2, primii 3.

Nul al cautarii: N_NULL istorii sintetice uniforme cu aceeasi structura de
zile; pe fiecare se reface tot ecranul nou, iar maximul z de dezvoltare al
fiecarei celule da p-ul cautarii.

Confirmare pe ultimele 30% romanesti, pentru selectia inghetata:
  (a) „depaseste clasamentul”: McNemar exact unilateral, extragerile la care
      candidatul atinge tinta si liderul nu, fata de invers;
  (b) fata de hazard: binomial exact unilateral fata de rata hipergeometrica.
  Holm separat pe (a) si pe (b), peste toti candidatii inghetati.
  (c) Replicare externa, unde exista (6/49 -> sase 6/49 straine; Joker Urna 1
      -> 6/45 Austria, Belgia, Ungaria si EuroMillions 5/50): aceeasi formula,
      acelasi K si aceeasi tinta, z pe suma evenimentelor fata de suma
      asteptata, unilateral.
Intra in aplicatie numai candidatul cu Holm(a) < 0.05, Holm(b) < 0.05 si,
unde exista, p(c) < 0.05.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import os
import sys
import time
import warnings
from concurrent.futures import ProcessPoolExecutor
from itertools import combinations
from math import sqrt
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.signal import lfilter
from scipy.stats import binom, binomtest, norm, rankdata

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "prediction_screen_20261005", HERE / "prediction_screen_2026-10-05.py"
)
ps = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ps)

ROOT = ps.ROOT
sys.path.insert(0, str(ROOT))

SEED = 20261010
N_NULL = int(os.environ.get("METHOD_SCREEN_NULL", 200))
WORKERS = int(os.environ.get("METHOD_SCREEN_WORKERS", 4))
OUT = Path(os.environ.get("METHOD_SCREEN_OUT", HERE / "method_screen_2026-10-10.json"))

GAMES = {
    "ro_649": {**ps.GAMES["ro_649"], "Ks": (6, 11, 16), "targets": (3, 4), "bench": "loto_6_49"},
    "ro_540": {**ps.GAMES["ro_540"], "Ks": (8, 11, 16), "targets": (4,), "bench": "loto_5_40"},
    "ro_joker1": {**ps.GAMES["ro_joker1"], "Ks": (8, 11, 16), "targets": (3, 4), "bench": "joker_urna1"},
    "ro_joker2": {**ps.GAMES["ro_joker2"], "Ks": (1,), "targets": (1,), "bench": "joker_urna2"},
}
EXTERNAL = {"ro_649": ps.EXTERNAL["ro_649"], "ro_joker1": ps.EXTERNAL["ro_joker1"]}
TIE_GATE = ps.TIE_GATE


def load_game(game: str):
    cfg = GAMES[game]
    return ps.load(cfg["path"], cfg["cols"], cfg["N"])


def cells(game: str) -> list[tuple[int, int]]:
    cfg = GAMES[game]
    return [(K, t) for K in cfg["Ks"] for t in cfg["targets"]]


# ------------------------------------------------------------------ helpers


def fir_past(C: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """S[d] = sum_l weights[l-1] * C[d-l], l = 1..len(weights): numai zilele trecute."""
    b = np.concatenate([[0.0], np.asarray(weights, dtype=float)])
    return lfilter(b, [1.0], C, axis=0)


def ewma(C: np.ndarray, a: float) -> np.ndarray:
    return ps.ewma_state(C, a)


def own_gap_features(Ib: np.ndarray, G: np.ndarray, Ms=(10, 20, 50, 512), shrink=(5.0, 20.0, 80.0), gmax=100):
    """Golul curent fata de golurile complete ale aceluiasi numar (numai trecutul)."""
    n_days, N = Ib.shape
    cap = 512
    buf = np.full((N, cap), np.nan)
    cnt = np.zeros(N, dtype=np.int64)
    out = {f"pct{M}": np.zeros((n_days, N)) for M in Ms}
    out.update({f"z{M}": np.zeros((n_days, N)) for M in Ms})
    out.update({f"med{M}": np.zeros((n_days, N)) for M in Ms})
    out.update({f"cv{M}": np.zeros((n_days, N)) for M in Ms})
    ev = np.zeros((N, gmax + 1))
    risk = np.zeros((N, gmax + 1))
    ev_all = np.zeros(gmax + 1)
    risk_all = np.zeros(gmax + 1)
    for s in shrink:
        out[f"hz{int(s)}"] = np.zeros((n_days, N))
    rows = np.arange(N)
    for d in range(n_days):
        g = G[d]
        for M in Ms:
            m = min(M, cap)
            # ultimele m goluri complete ale fiecarui numar (inel circular)
            idx = (cnt[:, None] - 1 - np.arange(m)[None, :]) % cap
            vals = buf[rows[:, None], idx]
            valid = (np.arange(m)[None, :] < np.minimum(cnt, cap)[:, None]) & np.isfinite(vals)
            n_ok = valid.sum(1)
            safe = np.where(valid, vals, 0.0)
            with np.errstate(invalid="ignore", divide="ignore"), warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                mean = safe.sum(1) / np.maximum(n_ok, 1)
                var = (np.where(valid, (vals - mean[:, None]) ** 2, 0.0)).sum(1) / np.maximum(n_ok - 1, 1)
                sd = np.sqrt(var)
                le = (np.where(valid, vals <= g[:, None], False)).sum(1)
                out[f"pct{M}"][d] = np.where(n_ok > 0, (le + 0.5) / (n_ok + 1.0), 0.5)
                out[f"z{M}"][d] = np.where(n_ok > 1, (g - mean) / np.maximum(sd, 1.0), 0.0)
                med = np.nanmedian(np.where(valid, vals, np.nan), axis=1) if n_ok.any() else np.full(N, np.nan)
                out[f"med{M}"][d] = np.where(n_ok > 0, g / np.maximum(np.nan_to_num(med, nan=1.0), 1.0), 1.0)
                out[f"cv{M}"][d] = np.where(n_ok > 1, sd / np.maximum(mean, 1.0), 0.0)
        b = np.minimum(g, gmax).astype(np.int64)
        p_all = (ev_all + 1.0) / (risk_all + 2.0)
        for s in shrink:
            out[f"hz{int(s)}"][d] = (ev[rows, b] + s * p_all[b]) / (risk[rows, b] + s)
        # numerele aparute azi incheie un gol de lungime g
        hit = np.flatnonzero(Ib[d] > 0)
        for i in hit:
            L = int(g[i])
            buf[i, cnt[i] % cap] = L
            cnt[i] += 1
            Lb = min(L, gmax)
            risk[i, 1 : Lb + 1] += 1.0
            ev[i, Lb] += 1.0
            risk_all[1 : Lb + 1] += 1.0
            ev_all[Lb] += 1.0
    return out


def markov_window(Ib: np.ndarray, lag: int, L: int | None, a: float = 20.0) -> np.ndarray:
    """Ca ps.markov, cu tranzitiile numarate numai pe ultimele L zile (None = tot)."""
    n_days, N = Ib.shape
    T = np.zeros((N, N))
    ni = np.zeros(N)
    base = np.zeros(N)
    S = np.zeros((n_days, N))
    for d in range(n_days):
        days_seen = d if L is None else min(d, L)
        p = (base + 1.0) / (days_seen + 2.0)
        if d - lag >= 0:
            idx = np.flatnonzero(Ib[d - lag])
            if len(idx):
                S[d] = (((T[idx] + a * p) / (ni[idx, None] + a)) - p).sum(axis=0)
        # tranzitia (d-lag -> d) intra dupa scorul zilei d
        if d - lag >= 0:
            T += np.outer(Ib[d - lag], Ib[d])
            ni += Ib[d - lag]
        base += Ib[d]
        if L is not None:
            old = d - L
            if old - lag >= 0:
                T -= np.outer(Ib[old - lag], Ib[old])
                ni -= Ib[old - lag]
            if old >= 0:
                base -= Ib[old]
    return S


def self_transition(Ib: np.ndarray, lag: int, a: float) -> np.ndarray:
    """P(numarul apare | starea lui acum `lag` zile) - P(apare), numai din trecut."""
    n_days, N = Ib.shape
    prev = np.zeros_like(Ib)
    prev[lag:] = Ib[:-lag]
    both = ps.cumsum0(prev * Ib)[:-1]  # perechi (t-lag, t) cu t < d
    prev_c = ps.cumsum0(prev)[:-1]
    app = ps.cumsum0(Ib)[:-1]
    known = (np.arange(n_days) >= lag)[:, None]
    app_known = ps.cumsum0(Ib * known)[:-1]  # aparitii la zile t >= lag, t < d
    t = np.arange(n_days)[:, None].astype(float)
    p = (app + 1.0) / (t + 2.0)
    valid_t = np.maximum(t - lag, 0.0)  # zile t < d cu starea de la t-lag cunoscuta
    p_on = (both + a * p) / (prev_c + a)
    app_off = app_known - both
    p_off = (app_off + a * p) / (np.maximum(valid_t - prev_c, 0.0) + a)
    state = np.zeros_like(Ib)
    state[lag:] = Ib[:-lag]
    return np.where(state > 0, p_on, p_off) - p


def cooc_window_scores(Ib: np.ndarray, CS: np.ndarray, ws, Ls, a_list, ew_states) -> dict:
    """Co-aparitie pe fereastra L: cu numerele ultimelor w zile si cu starile EWMA."""
    n_days, N = Ib.shape
    out = {}
    for L in Ls:
        M = np.zeros((N, N))
        A = np.zeros(N)
        res = {}
        for w in ws:
            for a in a_list:
                res[("w", w, a)] = np.zeros((n_days, N))
        with_ew = L in (500, None)
        if with_ew:
            for key in ew_states:
                res[("e", key, 20.0)] = np.zeros((n_days, N))
        for d in range(n_days):
            if A.sum() > 0:
                p = (A + 1.0) / (A.sum() + N)
                for a in a_list:
                    P = (M + a * p[None, :]) / (A[:, None] + a) - p[None, :]
                    np.fill_diagonal(P, 0.0)
                    for w in ws:
                        r = CS[d] - CS[max(0, d - w)]
                        if r.any():
                            res[("w", w, a)][d] = r @ P
                    if a == 20.0 and with_ew:
                        for key, E in ew_states.items():
                            res[("e", key, 20.0)][d] = E[d] @ P
            o = np.outer(Ib[d], Ib[d])
            np.fill_diagonal(o, 0.0)
            M += o
            A += Ib[d]
            if L is not None and d - L >= 0:
                o2 = np.outer(Ib[d - L], Ib[d - L])
                np.fill_diagonal(o2, 0.0)
                M -= o2
                A -= Ib[d - L]
        for k, v in res.items():
            out[(L,) + k] = v
    return out


def lift_window(Ib: np.ndarray, L: int | None, with_lift: bool = True):
    """Generator (d, lift NxN fara diagonala) cu co-aparitiile zilelor < d din fereastra L."""
    n_days, N = Ib.shape
    M = np.zeros((N, N))
    A = np.zeros(N)
    n = 0
    for d in range(n_days):
        if with_lift and n > 0 and A.sum() > 0:
            p = (A + 1.0) / (n + 2.0)
            joint = (M + 1.0) / (n + 2.0)
            lift = joint / (p[:, None] * p[None, :]) - 1.0
            np.fill_diagonal(lift, 0.0)
            yield d, lift, M
        else:
            yield d, None, M
        o = np.outer(Ib[d], Ib[d])
        np.fill_diagonal(o, 0.0)
        M += o
        A += Ib[d]
        n += 1
        if L is not None and d - L >= 0:
            o2 = np.outer(Ib[d - L], Ib[d - L])
            np.fill_diagonal(o2, 0.0)
            M -= o2
            A -= Ib[d - L]
            n -= 1


def pair_completion(Ib: np.ndarray, CS: np.ndarray, hot_ws, ks, L) -> dict:
    n_days, N = Ib.shape
    out = {(w, k): np.zeros((n_days, N)) for w in hot_ws for k in ks}
    for d, lift, _M in lift_window(Ib, L):
        if lift is None:
            continue
        for w in hot_ws:
            f = CS[d] - CS[max(0, d - w)]
            srt = np.sort(f)[::-1]
            for k in ks:
                thr = srt[k - 1]
                hot = (f >= thr) & (f > 0)
                if hot.any():
                    out[(w, k)][d] = lift[hot].sum(axis=0)
    return out


def pagerank_window(Ib: np.ndarray, Ls, damps, iters: int = 30) -> dict:
    n_days, N = Ib.shape
    out = {}
    for L in Ls:
        res = {dm: np.zeros((n_days, N)) for dm in damps}
        pis = {dm: np.full(N, 1.0 / N) for dm in damps}
        for d, _lift, M in lift_window(Ib, L, with_lift=False):
            deg = M.sum(1)
            if deg.sum() <= 0:
                continue
            W = M / np.maximum(deg[:, None], 1e-12)
            for dm in damps:
                pi = pis[dm]
                for _ in range(iters):
                    pi = (1.0 - dm) / N + dm * (W.T @ pi)
                    pi /= pi.sum()
                pis[dm] = pi
                res[dm][d] = pi
        for dm in damps:
            out[(L, dm)] = res[dm]
    return out


def holt(C: np.ndarray, alpha: float, beta: float) -> np.ndarray:
    n_days, N = C.shape
    S = np.zeros((n_days, N))
    lvl = np.zeros(N)
    tr = np.zeros(N)
    started = False
    for d in range(n_days):
        S[d] = lvl + tr
        x = C[d]
        if not started:
            lvl = x.astype(float)
            started = True
            continue
        new = alpha * x + (1 - alpha) * (lvl + tr)
        tr = beta * (new - lvl) + (1 - beta) * tr
        lvl = new
    return S


def cusum(C: np.ndarray, k: float, lam: float) -> np.ndarray:
    n_days, N = C.shape
    S = np.zeros((n_days, N))
    s = np.zeros(N)
    tot = np.zeros(N)
    for d in range(n_days):
        S[d] = s
        p = (tot + 1.0) / (d + 2.0)
        sd = np.sqrt(p * (1 - p))
        s = np.maximum(0.0, lam * s + (C[d] - p - k * sd))
        tot += C[d]
    return S


def slope(C: np.ndarray, W: int) -> np.ndarray:
    lags = np.arange(1, W + 1)
    w = (W + 1) / 2.0 - lags  # timpul relativ fata de media ferestrei
    return fir_past(C, w / (w**2).sum())


def knn_analog(state: np.ndarray, Y: np.ndarray, ks, warm: int = 30) -> dict:
    """Media iesirilor (Y[j]) la cele mai asemanatoare k stari trecute (j < d), minus media."""
    n_days, N = state.shape
    X = state - state.mean(axis=1, keepdims=True)
    nrm = np.linalg.norm(X, axis=1)
    Xn = X / np.maximum(nrm[:, None], 1e-12)
    Gm = Xn @ Xn.T
    CY = ps.cumsum0(Y)
    out = {k: np.zeros((n_days, N)) for k in ks}
    for d in range(warm, n_days):
        # Rotunjit: aceeasi asemanare nu trebuie sa difere prin ordinea adunarii
        # dupa o redenumire a numerelor; egalitatea merge la ziua mai recenta.
        sims = np.round(Gm[d, :d], 9)
        order = np.lexsort((-np.arange(d), -sims))  # egalitate: ziua mai recenta
        base = CY[d] / d
        for k in ks:
            if k <= d:
                out[k][d] = Y[order[:k]].mean(axis=0) - base
    return out


def hazard_window(Ib: np.ndarray, G: np.ndarray, L: int, gmax: int) -> np.ndarray:
    n_days, N = Ib.shape
    b = np.minimum(G, gmax).astype(np.int64)
    R = np.zeros((n_days, gmax + 1))
    E = np.zeros((n_days, gmax + 1))
    for d in range(n_days):
        np.add.at(R[d], b[d], 1.0)
        np.add.at(E[d], b[d], Ib[d])
    CR, CE = ps.cumsum0(R), ps.cumsum0(E)
    idx = np.arange(n_days)
    lo = np.maximum(0, idx - L)
    Rw, Ew = CR[idx] - CR[lo], CE[idx] - CE[lo]
    tot_r, tot_e = Rw.sum(1), Ew.sum(1)
    p = (tot_e + 1.0) / (tot_r + 2.0)
    return (Ew[idx[:, None], b] + 2.0 * p[:, None]) / (Rw[idx[:, None], b] + 2.0)


def spectral(Ib: np.ndarray, W: int, top: int, every: int = 20) -> np.ndarray:
    n_days, N = Ib.shape
    S = np.zeros((n_days, N))
    for start in range(W, n_days, every):
        seg = Ib[start - W : start] - Ib[start - W : start].mean(axis=0)
        F = np.fft.rfft(seg, axis=0)
        F[0] = 0.0
        amp = np.abs(F)
        keep = np.argsort(-amp, axis=0, kind="stable")[:top]
        mask = np.zeros_like(amp, dtype=bool)
        np.put_along_axis(mask, keep, True, axis=0)
        Fk = np.where(mask, F, 0.0)
        freqs = np.fft.rfftfreq(W)
        end = min(start + every, n_days)
        t = np.arange(W, W + (end - start))[:, None]  # extrapolare dupa fereastra
        phase = np.exp(2j * np.pi * freqs[None, :] * t)
        S[start:end] = (2.0 / W) * np.real(phase @ Fk)
    return S


def rank01(S: np.ndarray) -> np.ndarray:
    """Rangul pe zi, cu rang mediu la egalitate: nu depinde de eticheta numarului.

    Scorul se rotunjeste intai: o suma facuta in alta ordine dupa redenumire nu
    trebuie sa desparta doua valori egale."""
    return (rankdata(np.round(S, 9), axis=1, method="average") - 1.0) / max(1, S.shape[1] - 1)


def ridge_online(feats: list[np.ndarray], Ib: np.ndarray, lam: float, warm: int, every: int = 100) -> np.ndarray:
    n_days, N = Ib.shape
    F = np.stack([rank01(f) - 0.5 for f in feats], axis=2)
    F = np.concatenate([np.ones((n_days, N, 1)), F], axis=2)
    S = np.zeros((n_days, N))
    lo = 50
    for fit_at in range(max(warm, lo + 50), n_days, every):
        X = F[lo:fit_at].reshape(-1, F.shape[2])
        y = Ib[lo:fit_at].ravel()
        reg = lam * np.eye(F.shape[2])
        reg[0, 0] = 0.0
        w = np.linalg.solve(X.T @ X + reg, X.T @ y)
        end = min(fit_at + every, n_days)
        S[fit_at:end] = F[fit_at:end] @ w
    return S


# ------------------------------------------------------------------- the bank


def bank(data):
    """Generator (nume, familie, scor zile x numere); S[d] vede numai zilele < d."""
    N, Ib, C = data.N, data.Ib, data.C
    single = data.d == 1
    CS = ps.cumsum0(C)
    G = ps.gaps(Ib)

    def both(name, fam, S):
        yield name, fam, S
        yield f"anti_{name}", fam, -S

    for beta in (0.3, 0.5, 0.75, 1.0, 1.5, 2.0):
        for H in (50, 100, 200, 500, 1000):
            yield from both(f"pow_b{beta}_h{H}", "F01_powerlaw", fir_past(C, np.arange(1, H + 1) ** -beta))

    alphas = (0.005, 0.01, 0.02, 0.03, 0.05, 0.08, 0.12, 0.2, 0.3, 0.5)
    ew = {a: ewma(C, a) for a in alphas}
    for a_fast, a_slow in combinations(sorted(alphas, reverse=True), 2):
        yield from both(f"macd_{a_fast}_{a_slow}", "F02_macd", ew[a_fast] - ew[a_slow])

    og = own_gap_features(Ib, G)
    for key, S in og.items():
        if key.startswith("cv"):
            continue
        yield from both(f"owngap_{key}", "F03_owngap", S)

    mk_all = {}
    for lag in range(1, 11):
        mk_all[lag] = markov_window(Ib, lag, None)
        if lag in (5, 6, 8, 9, 10):
            yield from both(f"markov_all_lag{lag}", "F04_markov_window", mk_all[lag])
        for L in (200, 500, 1000):
            yield from both(f"markov_w{L}_lag{lag}", "F04_markov_window", markov_window(Ib, lag, L))

    for lag in range(1, 11):
        for a in (10.0, 50.0):
            yield from both(f"selftr_lag{lag}_a{int(a)}", "F05_selftransition", self_transition(Ib, lag, a))

    if not single:
        cw = cooc_window_scores(
            Ib, CS, ws=(1, 2, 3, 5, 10), Ls=(100, 200, 500, None), a_list=(5.0, 20.0),
            ew_states={0.03: ew[0.03], 0.12: ew[0.12], 0.2: ew[0.2], 0.3: ew[0.3]},
        )
        for (L, kind, w, a), S in cw.items():
            tagL = "all" if L is None else L
            if kind == "w":
                yield from both(f"cooc_w{w}_L{tagL}_a{int(a)}", "F06_cooc_window", S)
            elif L in (500, None):
                yield from both(f"cooc_ewma{w}_L{tagL}", "F07_cooc_ewma", S)
        for L in (500, None):
            pcs = pair_completion(Ib, CS, hot_ws=(20, 50, 100), ks=(3, 6, 10), L=L)
            for (w, k), S in pcs.items():
                yield from both(f"paircomp_w{w}_k{k}_L{'all' if L is None else L}", "F08_paircomp", S)
        pr = pagerank_window(Ib, Ls=(50, 100, 200, 500, None), damps=(0.5, 0.85, 0.95))
        for (L, dm), S in pr.items():
            yield from both(f"pagerank_L{'all' if L is None else L}_d{dm}", "F09_pagerank", S)

    for al in (0.01, 0.02, 0.05, 0.1):
        for be in (0.01, 0.05, 0.2):
            yield from both(f"holt_a{al}_b{be}", "F10_holt", holt(C, al, be))

    for k in (0.25, 0.5, 1.0):
        for lam in (1.0, 0.99):
            yield from both(f"cusum_k{k}_l{lam}", "F11_cusum", cusum(C, k, lam))

    for W in (30, 60, 100, 200, 400, 800):
        yield from both(f"slope_{W}", "F12_slope", slope(C, W))

    for M in (10, 20, 50):
        cv = og[f"cv{M}"]
        yield from both(f"regular_cv{M}", "F13_regularity", -cv)
        yield from both(f"due_regular_{M}", "F13_regularity", og[f"z{M}"] / (1.0 + cv))

    states = {"last": ps.shift(C), "ewma0.3": ew[0.3], "ewma0.1": ewma(C, 0.1), "win50": ps.window(CS, 50)}
    for sname, st in states.items():
        for k, S in knn_analog(st, C, ks=(5, 10, 20, 50, 100)).items():
            yield from both(f"analog_{sname}_k{k}", "F14_analog", S)

    for Lmax in (3, 5, 10):
        for dec in (1.0, 0.7, 0.5):
            S = sum(dec ** (lag - 1) * mk_all[lag] for lag in range(1, Lmax + 1))
            yield from both(f"markovsum_L{Lmax}_d{dec}", "F15_markov_multi", S)

    for L in (300, 1000):
        for gmax in (50, 100):
            yield from both(f"hazwin_L{L}_g{gmax}", "F16_hazard_window", hazard_window(Ib, G, L, gmax))

    for W in (256, 512):
        for top in (1, 3):
            yield from both(f"spectral_W{W}_top{top}", "F17_spectral", spectral(Ib, W, top))

    base = {
        "win20": ps.window(CS, 20),
        "win100": ps.window(CS, 100),
        "winall": CS[:-1],
        "ewma0.05": ew[0.05],
        "ewma0.01": ew[0.01],
        "gapratio": G * (CS[:-1] + 1.0) / (np.arange(data.n_days)[:, None] + 1.0),
        "ownhz20": og["hz20"],
        "markov1": mk_all[1],
        "macd": ew[0.12] - ew[0.01],
        "pow1": fir_past(C, np.arange(1, 201) ** -1.0),
        "slope100": slope(C, 100),
    }
    if single:
        base["selftr1"] = self_transition(Ib, 1, 10.0)
    else:
        base["cooc"] = cw[(None, "w", 1, 20.0)]
    names = list(base)
    ranks = {n: rank01(base[n]) for n in names}
    for r in (2, 3):
        for combo in combinations(names, r):
            yield f"ens_{'+'.join(combo)}", "F18_rank_ensemble", sum(ranks[c] for c in combo) / r
    rng = np.random.default_rng(SEED)
    quads = set()
    while len(quads) < 80:
        quads.add(tuple(sorted(rng.choice(len(names), 4, replace=False).tolist())))
    for q in sorted(quads):
        combo = [names[i] for i in q]
        yield f"ens4_{'+'.join(combo)}", "F18_rank_ensemble", sum(ranks[c] for c in combo) / 4

    pool_feats = [base[n] for n in names] + [ew[0.3], ew[0.02], og["pct50"], og["z20"]]
    rng = np.random.default_rng(SEED + 1)
    subsets = set()
    while len(subsets) < 25:
        subsets.add(tuple(sorted(rng.choice(len(pool_feats), 5, replace=False).tolist())))
    for j, sub in enumerate(sorted(subsets)):
        for lam in (1.0, 100.0):
            yield f"ridge_s{j}_l{int(lam)}", "F19_ridge", ridge_online([pool_feats[i] for i in sub], Ib, lam, ps.WARMUP_DAYS)


# ---------------------------------------------------------------- evaluation


def tail(N: int, d: int, K: int, t: int) -> float:
    return ps.hyp_tail(N, d, K, t)


def screen(data, game: str, keep_hits: bool = False):
    m = ps.masks(data)
    Ks = GAMES[game]["Ks"]
    rows, store = [], {}
    for name, family, S in bank(data):
        if not np.all(np.isfinite(S)):
            continue
        ev = ps.evaluate(S, data, Ks)
        for K, (hits, tie) in ev.items():
            tie_rate = float(tie[m["dev"]].mean())
            h1 = ps.stats(hits, m["dev1"], data.N, data.d, K, 1)
            h2 = ps.stats(hits, m["dev2"], data.N, data.d, K, 1)
            for t in GAMES[game]["targets"]:
                if (K, t) not in cells(game):
                    continue
                dev = ps.stats(hits, m["dev"], data.N, data.d, K, t)
                rows.append({
                    "name": name, "family": family, "K": K, "target": t,
                    "tie_rate": tie_rate, "gated": tie_rate > TIE_GATE,
                    "stable": h1["excess_hits"] > 0 and h2["excess_hits"] > 0,
                    "dev_z_hits": dev["z_hits"], "dev_z_event": dev["z_event"],
                    "dev_rate": dev["rate"], "dev_p0": dev["p0"], "dev_n": dev["n"],
                })
            if keep_hits:
                store[(name, K)] = hits
    return pd.DataFrame(rows), store, m


def cell_max(table: pd.DataFrame) -> dict:
    ok = table[(~table["gated"]) & table["stable"]]
    out = {}
    for (K, t), grp in ok.groupby(["K", "target"]):
        out[f"{K}_{t}"] = {"z_event": float(grp["dev_z_event"].max()), "z_hits": float(grp["dev_z_hits"].max())}
    return out


def _null_one(args):
    game, seed = args
    logging.disable(logging.WARNING)
    base = load_game(game)
    syn = ps.synthetic(base, np.random.default_rng(seed))
    table, _, _ = screen(syn, game)
    return cell_max(table)


def select(table: pd.DataFrame, game: str) -> list[dict]:
    ok = table[(~table["gated"]) & table["stable"]]
    picked = []
    for K, t in cells(game):
        grp = ok[(ok["K"] == K) & (ok["target"] == t)]
        plan = (("dev_z_event", 3),) if game == "ro_joker2" else (("dev_z_event", 2), ("dev_z_hits", 1))
        names: set[str] = set()
        for col, n in plan:
            taken = 0
            for _, r in grp.sort_values([col, "name"], ascending=[False, True]).iterrows():
                if r["name"] in names:
                    continue
                names.add(r["name"])
                picked.append({"game": game, "K": int(K), "target": int(t), "name": r["name"],
                               "family": r["family"], "by": col, "dev_z_event": float(r["dev_z_event"]),
                               "dev_z_hits": float(r["dev_z_hits"]), "dev_rate": float(r["dev_rate"]),
                               "dev_p0": float(r["dev_p0"])})
                taken += 1
                if taken == n:
                    break
    return picked


# ---------------------------------------------------------------- leaders


def leaders(game: str) -> dict[tuple[int, int], str]:
    import loto_enterprise.benchmark.decision as dec

    folds = pd.read_csv(ROOT / "bench_results" / "folds.csv")
    if "failed" not in folds.columns:
        folds["failed"] = False
    out = {}
    for K, t in cells(game):
        dec.BENCH_HIT_TARGET = 3 if t == 1 else t
        cfg = dec.decide_optimal_config_for_pool(
            folds, GAMES[game]["bench"], pool_size=K, draw_n=len(GAMES[game]["cols"]), max_num=GAMES[game]["N"]
        )
        members = [m["method"] for m in (cfg.get("ensemble") or [])]
        out[(K, t)] = members[0] if members else "frequency"
    return out


def _leader_day(args):
    """Clasamentul liderului pentru o zi (call_method pe randurile dinaintea zilei)."""
    name, game, first_row = args
    logging.disable(logging.WARNING)
    from loto_enterprise.benchmark.methods import call_method
    from loto_enterprise.core.ranking import rank_by_score
    from loto_enterprise.core.score_validation import has_usable_score_variance

    data = _LEADER_DATA.get(game)
    if data is None:
        data = _LEADER_DATA[game] = load_game(game)
    N = data.N
    prefix = data.draws[:first_row]
    scores, _ = call_method(name, prefix, N)
    fallback = False
    if not has_usable_score_variance(scores):
        scores, _ = call_method("frequency", prefix, N)
        fallback = True
    ranked = rank_by_score({int(k): float(v) for k, v in scores.items()}, N)
    return ranked, fallback


_LEADER_DATA: dict = {}


def leader_hits(game: str, data, name: str, rows_mask: np.ndarray, pool: ProcessPoolExecutor) -> tuple[dict, int]:
    """hits[K] pe randurile din masca (-1 in afara ei) si numarul de zile pe rezerva."""
    days = np.unique(data.day[rows_mask])
    first = {int(d): int(np.flatnonzero(data.day == d)[0]) for d in days}
    jobs = [(name, game, first[int(d)]) for d in days]
    res = list(pool.map(_leader_day, jobs, chunksize=16))
    by_day = {int(d): r for d, r in zip(days, res)}
    out = {}
    for K in GAMES[game]["Ks"]:
        h = np.full(len(data.draws), -1, dtype=np.int16)
        for i in np.flatnonzero(rows_mask):
            top = set(by_day[int(data.day[i])][0][:K])
            h[i] = len(top & set(int(x) for x in data.draws[i]))
        out[K] = h
    return out, sum(1 for r in res if r[1])


def mcnemar_greater(new_ev: np.ndarray, lead_ev: np.ndarray) -> tuple[int, int, float]:
    b = int((new_ev & ~lead_ev).sum())
    c = int((~new_ev & lead_ev).sum())
    n = b + c
    p = float(binom.sf(b - 1, n, 0.5)) if n else 1.0
    return b, c, p


# ---------------------------------------------------------------- external


def _external_series(args):
    """Hiturile candidatilor ceruti pe o serie straina (o singura trecere prin banca)."""
    path, cols, N, wanted = args
    logging.disable(logging.WARNING)
    data = ps.load(Path(path), cols, N)
    mask = ps.masks(data)["all"]
    names = {name for name, _K in wanted}
    Ks = sorted({K for _n, K in wanted})
    out = {}
    for name, _f, S in bank(data):
        if name in names:
            ev = ps.evaluate(S, data, Ks)
            for K in Ks:
                if (name, K) in wanted:
                    out[(name, K)] = ev[K][0][mask]
    return {"d": data.d, "N": N, "hits": out}


def external_tests(frozen: list[dict], pool: ProcessPoolExecutor) -> None:
    """Replicarea externa: z pe suma evenimentelor fata de suma asteptata, unilateral."""
    jobs, keys = [], []
    for game, rows in EXTERNAL.items():
        wanted = {(f["name"], f["K"]) for f in frozen if f["game"] == game}
        if not wanted:
            continue
        for label, path, cols, N in rows:
            jobs.append((str(path), cols, N, wanted))
            keys.append((game, label))
    results = dict(zip(keys, pool.map(_external_series, jobs)))
    for f in frozen:
        if f["game"] not in EXTERNAL:
            f["external"] = None
            continue
        ev_sum, exp_sum, var_sum, per = 0, 0.0, 0.0, {}
        for (game, label), res in results.items():
            if game != f["game"]:
                continue
            hits = res["hits"][(f["name"], f["K"])]
            n = int(len(hits))
            p0 = tail(res["N"], res["d"], f["K"], f["target"])
            e = int((hits >= f["target"]).sum())
            ev_sum += e
            exp_sum += n * p0
            var_sum += n * p0 * (1 - p0)
            per[label] = {"n": n, "events": e, "rate": e / n if n else 0.0, "p0": p0}
        z = (ev_sum - exp_sum) / sqrt(var_sum) if var_sum > 0 else 0.0
        f["external"] = {"events": ev_sum, "expected": exp_sum, "z": z, "p": float(norm.sf(z)), "series": per}


# ---------------------------------------------------------------- checks


def leak_check(data) -> int:
    """Schimba extragerile unei zile; scorurile pana la acea zi inclusiv raman identice."""
    d0 = data.n_days // 2
    draws = data.draws.copy()
    rng = np.random.default_rng(7)
    for r in np.flatnonzero(data.day == d0):
        draws[r] = rng.permutation(data.N)[: data.d] + 1
    alt = ps.Data(draws, data.day, data.day_dates, data.N, data.ordered)
    checked = 0
    for (n1, _f1, S1), (n2, _f2, S2) in zip(bank(data), bank(alt)):
        assert n1 == n2
        if not np.allclose(S1[: d0 + 1], S2[: d0 + 1], rtol=0, atol=1e-12):
            raise AssertionError(f"leak in {n1}")
        checked += 1
    return checked


def equivariance_check(data) -> int:
    """Redenumeste numerele; fiecare scor trebuie sa urmeze numarul (permutat odata cu el)."""
    rng = np.random.default_rng(11)
    perm = rng.permutation(data.N)  # numarul n devine perm[n-1]+1
    alt = ps.Data(perm[data.draws - 1] + 1, data.day, data.day_dates, data.N, data.ordered)
    checked = 0
    for (n1, _f1, S1), (n2, _f2, S2) in zip(bank(data), bank(alt)):
        assert n1 == n2
        if not np.allclose(S1, S2[:, perm], rtol=1e-9, atol=1e-9):
            raise AssertionError(f"not equivariant: {n1}")
        checked += 1
    return checked


def data_fingerprint() -> dict[str, str]:
    paths = {cfg["path"] for cfg in GAMES.values()}
    paths |= {path for rows in EXTERNAL.values() for _l, path, _c, _n in rows}
    paths.add(ROOT / "bench_results" / "folds.csv")
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


# ---------------------------------------------------------------- main


def main() -> None:
    logging.disable(logging.WARNING)
    t0 = time.time()
    fp = data_fingerprint()
    report: dict = {"protocol": __doc__, "data_sha256": fp, "n_null": N_NULL, "games": {}}
    games = sys.argv[1:] or list(GAMES)

    d649 = load_game("ro_649")
    print(f"[leak] {leak_check(d649)} scoreri fara date viitoare", flush=True)
    print(f"[equivariance] {equivariance_check(d649)} scoreri verificati", flush=True)

    frozen: list[dict] = []
    real = {}
    with ProcessPoolExecutor(WORKERS) as pool:
        for game in games:
            data = load_game(game)
            table, store, m = screen(data, game, keep_hits=True)
            n_scorers = table["name"].nunique()
            print(f"[{game}] scoreri={n_scorers} teste={len(table)} gated={int(table['gated'].sum())} "
                  f"({time.time() - t0:.0f}s)", flush=True)
            obs = cell_max(table)
            seeds = [(game, SEED + 1000 * (list(GAMES).index(game) + 1) + i) for i in range(N_NULL)]
            null = list(pool.map(_null_one, seeds))
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
            lead = leaders(game)
            lead_hits, lead_fb = {}, {}
            for name in sorted(set(lead.values())):
                lead_hits[name], lead_fb[name] = leader_hits(game, data, name, m["all"], pool)
            sel = select(table, game)
            for s in sel:
                hits = store[(s["name"], s["K"])]
                lh = lead_hits[lead[(s["K"], s["target"])]][s["K"]]
                conf = m["conf"]
                new_ev = hits[conf] >= s["target"]
                lead_ev = lh[conf] >= s["target"]
                b, c, p_mc = mcnemar_greater(new_ev, lead_ev)
                p0 = tail(data.N, data.d, s["K"], s["target"])
                n = int(conf.sum())
                e = int(new_ev.sum())
                s.update({
                    "leader": lead[(s["K"], s["target"])],
                    "leader_dev_rate": float((lh[m["dev"]] >= s["target"]).mean()),
                    "conf_n": n, "conf_events": e, "conf_rate": e / n,
                    "conf_leader_events": int(lead_ev.sum()), "conf_leader_rate": float(lead_ev.mean()),
                    "conf_p0": p0, "mcnemar_b": b, "mcnemar_c": c, "p_vs_leader": p_mc,
                    "p_vs_random": float(binomtest(e, n, p0, alternative="greater").pvalue),
                })
                frozen.append(s)
            real[game] = {"table": table, "leaders": lead}
            report["games"][game] = {
                "rows": int(len(data.draws)), "days": int(data.n_days), "scorers": int(n_scorers),
                "tests": int(len(table)), "gated": int(table["gated"].sum()),
                "families": table.groupby("family")["name"].nunique().to_dict(),
                "search": search,
                "leaders": {f"{K}_{t}": v for (K, t), v in lead.items()},
                "leader_fallback_days": lead_fb,
                "leader_rates": {
                    f"{K}_{t}": {
                        "dev": float((lead_hits[v][K][m["dev"]] >= t).mean()),
                        "conf": float((lead_hits[v][K][m["conf"]] >= t).mean()),
                        "p0": tail(data.N, data.d, K, t),
                    }
                    for (K, t), v in lead.items()
                },
            }
            if data_fingerprint() != fp:
                raise SystemExit("istoricul s-a schimbat in timpul rularii; rezultatul nu se scrie")

        external_tests(frozen, pool)
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
    if data_fingerprint() != fp:
        raise SystemExit("istoricul s-a schimbat in timpul rularii; rezultatul nu se scrie")
    OUT.write_text(json.dumps(report, indent=1, default=float), encoding="utf-8")
    print(f"[gata] {len(frozen)} inghetati, {len(report['promoted'])} promovati, {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
