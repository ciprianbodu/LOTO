"""Ecran predictiv extins, 2026-10-05: sute de scoreri x toate jocurile.

Protocol fixat inainte de rulare:

Date. Romania: 6/49 (n1..n6), 5/40 (n1..n6, sase extrase), Joker Urna 1
(n1..n5), Joker Urna 2 (coloana joker). Ordinea de extragere este pastrata.
Valid = exact draw_n intregi distincti in interval.

Taiere. Scorul unei zile vede numai zilele ANTERIOARE; toate extragerile din
aceeasi zi primesc acelasi scor. Primele 200 de zile = warmup. Dezvoltare =
primele 70% din zilele ramase; confirmare = ultimele 30%, evaluate numai pentru
selectia inghetata.

Candidati. ~490 de scoreri in 17 familii: frecvente pe ferestre, EWMA,
goluri si hazard, impuls, Markov pe decalaje, co-aparitie, geometria pe grila
biletului (8 asezari x 6 vecinatati x 3 memorii), relatii pe linia numerelor,
sume/diferente de perechi, ordinea bilelor, calendar, conditionare pe
extragerea precedenta, periodicitate, AR comun, regresie logistica online si
ansambluri. Fiecare la 4 marimi de pool (Urna 2: top-1). Egalitatile se rup
dupa frecventa totala anterioara, apoi dupa numarul mai mare; un candidat la
care numarul decide locul K in >50% din randurile de dezvoltare iese.

Metrica. Selectie: z al hiturilor totale in pool fata de media hipergeometrica
exacta. Secundar: z al evenimentului tinta (6/49 3+, 5/40 4+, Joker 3+,
Urna 2 numarul exact). Stabil = exces pozitiv pe ambele jumatati ale
dezvoltarii.

Nul. Sub extrageri uniforme independente, orice pool ales din trecut are
hituri exact hipergeometrice. Nulul cautarii = 200 de istorii sintetice iid
cu aceeasi structura de zile; pe fiecare se reface TOT ecranul si se retine
maximul z. Permutarea etichetelor nu e un nul valid pentru scorerii
echivarianti (frecventa ramane identica dupa redenumire).

Selectie inghetata pe dezvoltare: per joc, primii 3 candidati stabili dupa z
hituri, apoi primii 2 dupa z eveniment (nume distincte).

Confirmare: (1) Romania, ultimele 30%: binomial exact unilateral pe evenimentul
tinta si z pe hituri, Holm pe toate testele romanesti; (2) replicare externa,
aceeasi formula si acelasi K: 6/49 -> Germania, Cehia, Slovacia, Polonia,
Spania, Bulgaria; Joker Urna 1 -> 6/45 Austria, Belgia, Ungaria si
EuroMillions 5/50 (geometrie apropiata). Promovare numai daca evenimentul
tinta trece Holm < 0.05 in Romania SI replicarea externa, unde exista, are
p < 0.05 pe datele concatenate.
"""

from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from math import comb, sqrt
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.signal import lfilter
from scipy.stats import binomtest, norm

ROOT = Path(__file__).resolve().parents[2]
HIST = ROOT / "_ISTORIC"
EXT = HIST / "externe"

WARMUP_DAYS = 200
DEV_FRACTION = 0.70
N_NULL = 200
TIE_GATE = 0.50
SEED = 20261005
REFIT_EVERY = 100

N6 = [f"n{i}" for i in range(1, 7)]
N5 = [f"n{i}" for i in range(1, 6)]
GAMES = {
    "ro_649": {"path": HIST / "loto_6_49.csv", "cols": N6, "N": 49, "Ks": (6, 9, 12, 16), "target": 3},
    "ro_540": {"path": HIST / "loto_5_40.csv", "cols": N6, "N": 40, "Ks": (8, 10, 12, 16), "target": 4},
    "ro_joker1": {"path": HIST / "joker.csv", "cols": N5, "N": 45, "Ks": (8, 11, 14, 16), "target": 3},
    "ro_joker2": {"path": HIST / "joker.csv", "cols": ["joker"], "N": 20, "Ks": (1,), "target": 1},
}
EXTERNAL = {
    "ro_649": [
        ("germania", EXT / "germania_lotto_6aus49.csv", N6, 49),
        ("cehia", EXT / "cehia_sportka_6din49.csv", N6, 49),
        ("slovacia", EXT / "slovacia_loto_6din49.csv", N6, 49),
        ("polonia", EXT / "polonia_lotto_6din49.csv", N6, 49),
        ("spania", EXT / "spania_la_primitiva_6din49.csv", N6, 49),
        ("bulgaria", EXT / "bulgaria_toto2_6din49.csv", N6, 49),
    ],
    "ro_joker1": [
        ("austria", EXT / "austria_lotto_6aus45.csv", N6, 45),
        ("belgia", EXT / "belgia_lotto_6din45.csv", N6, 45),
        ("ungaria", EXT / "ungaria_hatoslotto_6din45.csv", N6, 45),
        ("euromillions", EXT / "euromillions_5din50_stele.csv", N5, 50),
    ],
}


# ----------------------------------------------------------------------- data


class Data:
    def __init__(self, draws: np.ndarray, day: np.ndarray, day_dates: np.ndarray, N: int, ordered: bool):
        self.draws = draws.astype(np.int64)
        self.day = day.astype(np.int64)
        self.N = N
        self.d = draws.shape[1]
        self.n_days = int(day[-1]) + 1
        self.day_dates = day_dates
        self.ordered = ordered
        C = np.zeros((self.n_days, N))
        np.add.at(C, (np.repeat(self.day, self.d), self.draws.ravel() - 1), 1.0)
        self.C = C
        self.Ib = (C > 0).astype(float)
        if ordered and self.d > 1:
            P = np.zeros((self.n_days, self.d, N))
            for p in range(self.d):
                np.add.at(P[:, p, :], (self.day, self.draws[:, p] - 1), 1.0)
            self.P = P
        else:
            self.P = None


def load(path: Path, cols: list[str], N: int) -> Data:
    df = pd.read_csv(path)
    dates = pd.to_datetime(df["date"], format="%d-%m-%Y", errors="coerce")
    nums = df[cols].apply(pd.to_numeric, errors="coerce")
    ok = dates.notna() & nums.notna().all(axis=1)
    arr = nums.loc[ok].to_numpy(dtype=float)
    dts = dates.loc[ok].to_numpy()
    integral = np.all(arr == np.round(arr), axis=1)
    arr_i = np.round(arr).astype(np.int64)
    in_range = np.all((arr_i >= 1) & (arr_i <= N), axis=1)
    distinct = np.array([len(set(r)) == len(r) for r in arr_i])
    keep = integral & in_range & distinct
    arr_i, dts = arr_i[keep], dts[keep]
    order = np.argsort(dts, kind="mergesort")
    arr_i, dts = arr_i[order], dts[order]
    day = np.zeros(len(arr_i), dtype=np.int64)
    day[1:] = np.cumsum(dts[1:] != dts[:-1])
    day_dates = dts[np.r_[0, np.flatnonzero(np.diff(day)) + 1]]
    sorted_frac = float(np.mean(np.all(np.diff(arr_i, axis=1) > 0, axis=1))) if arr_i.shape[1] > 1 else 1.0
    # Ordine aleatoare reala: sub 1/120 randuri sortate (5 numere); o arhiva partial
    # sortata (Germania ~15%) nu pastreaza ordinea bilelor.
    return Data(arr_i, day, day_dates, N, ordered=sorted_frac < 0.05)


def synthetic(base: Data, rng: np.random.Generator) -> Data:
    rows = len(base.draws)
    draws = np.argsort(rng.random((rows, base.N)), axis=1)[:, : base.d] + 1
    return Data(draws, base.day, base.day_dates, base.N, ordered=base.ordered)


# ------------------------------------------------------------------- helpers


def shift(X: np.ndarray) -> np.ndarray:
    out = np.zeros_like(X)
    out[1:] = X[:-1]
    return out


def ewma_state(X: np.ndarray, a: float) -> np.ndarray:
    y = lfilter([a], [1.0, -(1.0 - a)], X, axis=0)
    return shift(y)


def cumsum0(X: np.ndarray) -> np.ndarray:
    return np.vstack([np.zeros((1, X.shape[1])), np.cumsum(X, axis=0)])


def window(CS: np.ndarray, w: int) -> np.ndarray:
    n_days = CS.shape[0] - 1
    idx = np.arange(n_days)
    return CS[idx] - CS[np.maximum(0, idx - w)]


def day_rank(S: np.ndarray) -> np.ndarray:
    return np.argsort(np.argsort(S, axis=1, kind="stable"), axis=1) / max(1, S.shape[1] - 1)


def gaps(Ib: np.ndarray) -> np.ndarray:
    n_days, N = Ib.shape
    G = np.zeros((n_days, N))
    last = np.full(N, -1.0)
    for d in range(n_days):
        G[d] = d - last
        last[Ib[d] > 0] = d
    return G


def hazard_pooled(Ib: np.ndarray, G: np.ndarray, gmax: int = 100) -> np.ndarray:
    n_days, N = Ib.shape
    ev = np.zeros(gmax + 1)
    risk = np.zeros(gmax + 1)
    S = np.zeros((n_days, N))
    seen = 0.0
    hits = 0.0
    for d in range(n_days):
        b = np.minimum(G[d], gmax).astype(np.int64)
        p = (hits + 1.0) / (seen * N + 2.0)
        S[d] = (ev[b] + 2.0 * p) / (risk[b] + 2.0)
        np.add.at(risk, b, 1.0)
        np.add.at(ev, b, Ib[d])
        seen += 1.0
        hits += Ib[d].sum()
    return S


def markov(Ib: np.ndarray, lag: int, a: float = 20.0) -> np.ndarray:
    n_days, N = Ib.shape
    T = np.zeros((N, N))
    ni = np.zeros(N)
    base = np.zeros(N)
    S = np.zeros((n_days, N))
    for d in range(n_days):
        p = (base + 1.0) / (d + 2.0)
        if d - lag >= 0:
            prev = Ib[d - lag]
            idx = np.flatnonzero(prev)
            if len(idx):
                S[d] = (((T[idx] + a * p) / (ni[idx, None] + a)) - p).sum(axis=0)
            T += np.outer(prev, Ib[d])
            ni += prev
        base += Ib[d]
    return S


def cooccurrence(Ib: np.ndarray, alpha: float | None, a: float = 20.0) -> np.ndarray:
    n_days, N = Ib.shape
    M = np.zeros((N, N))
    A = np.zeros(N)
    S = np.zeros((n_days, N))
    for d in range(n_days):
        if d >= 1:
            idx = np.flatnonzero(Ib[d - 1])
            p = (A + 1.0) / (A.sum() + N) if A.sum() > 0 else np.full(N, 1.0 / N)
            if len(idx):
                S[d] = (((M[idx] + a * p) / (A[idx, None] + a)) - p).sum(axis=0)
        o = np.outer(Ib[d], Ib[d])
        np.fill_diagonal(o, 0.0)
        if alpha is None:
            M += o
            A += Ib[d]
        else:
            M = (1 - alpha) * M + alpha * o
            A = (1 - alpha) * A + alpha * Ib[d]
    return S


def conditional_freq(Ib: np.ndarray, key: np.ndarray, n_keys: int, prior: float) -> np.ndarray:
    """Frecventa conditionata de o cheie a zilei (cheia zilei d e cunoscuta la d)."""
    n_days, N = Ib.shape
    acc = np.zeros((n_keys, N))
    cnt = np.zeros(n_keys)
    tot = np.zeros(N)
    S = np.zeros((n_days, N))
    for d in range(n_days):
        k = int(key[d])
        base = (tot + 1.0) / (d + 2.0)
        S[d] = (acc[k] + prior * base) / (cnt[k] + prior)
        if k >= 0:
            acc[k] += Ib[d]
            cnt[k] += 1.0
        tot += Ib[d]
    return S


def online_tercile_key(values: np.ndarray, min_hist: int = 30) -> np.ndarray:
    """Cheia zilei d = tercila valorii zilei d-1 fata de valorile zilelor < d."""
    from bisect import insort

    n = len(values)
    key = np.ones(n, dtype=np.int64)
    hist: list[float] = []
    for d in range(1, n):
        insort(hist, float(values[d - 1]))
        if len(hist) < min_hist:
            continue
        m = len(hist)
        q1, q2 = hist[(m - 1) // 3], hist[(2 * (m - 1)) // 3]
        v = values[d - 1]
        key[d] = 0 if v <= q1 else 2 if v > q2 else 1
    return key


def day_properties(data: Data) -> dict[str, np.ndarray]:
    N, d = data.N, data.d
    props = {k: np.zeros(data.n_days) for k in ("sum", "span", "odd", "low", "consec", "maxgap")}
    cnt = np.zeros(data.n_days)
    for r, row in enumerate(data.draws):
        s = np.sort(row)
        dd = data.day[r]
        props["sum"][dd] += s.sum()
        props["span"][dd] += s[-1] - s[0]
        props["odd"][dd] += np.sum(s % 2)
        props["low"][dd] += np.sum(s <= N // 2)
        props["consec"][dd] += np.sum(np.diff(s) == 1) if d > 1 else 0
        props["maxgap"][dd] += np.max(np.diff(s)) if d > 1 else 0
        cnt[dd] += 1
    return {k: v / np.maximum(cnt, 1) for k, v in props.items()}


def pair_sum_diff(data: Data) -> tuple[np.ndarray, np.ndarray]:
    N = data.N
    PS = np.zeros((data.n_days, N))
    PD = np.zeros((data.n_days, N))
    for r, row in enumerate(data.draws):
        dd = data.day[r]
        for i in range(len(row)):
            for j in range(i + 1, len(row)):
                s = row[i] + row[j]
                if s <= N:
                    PS[dd, s - 1] += 1
                df = abs(row[i] - row[j])
                if df >= 1:
                    PD[dd, df - 1] += 1
    return PS, PD


def layouts(N: int) -> list[tuple[str, np.ndarray]]:
    i = np.arange(N)
    out = []
    for cols in (5, 6, 7, 8, 9, 10):
        if cols < N:
            out.append((f"row{cols}", np.stack((i // cols, i % cols), axis=1)))
    for rows in (7, 10):
        if rows < N:
            out.append((f"col{rows}", np.stack((i % rows, i // rows), axis=1)))
    return out


def grid_kernels(xy: np.ndarray) -> list[tuple[str, np.ndarray]]:
    dr = np.abs(xy[:, None, 0] - xy[None, :, 0])
    dc = np.abs(xy[:, None, 1] - xy[None, :, 1])
    ks = {
        "king": np.maximum(dr, dc) == 1,
        "rook": (dr + dc) == 1,
        "row": (dr == 0) & (dc > 0),
        "col": (dc == 0) & (dr > 0),
        "diag": (dr == dc) & (dr > 0),
        "knight": ((dr == 1) & (dc == 2)) | ((dr == 2) & (dc == 1)),
    }
    return [(k, v.astype(float)) for k, v in ks.items()]


def line_kernels(N: int) -> list[tuple[str, np.ndarray]]:
    n = np.arange(1, N + 1)
    a, b = n[:, None], n[None, :]
    rev = np.array([int(str(x)[::-1]) if x >= 10 else x * 10 for x in n])
    ks = {f"dist{k}": np.abs(a - b) == k for k in (1, 2, 3, 4, 5, 7, 10)}
    ks["mirror"] = (a + b) == N + 1
    ks["reverse"] = (rev[:, None] == b) | (rev[None, :] == a)
    ks["lastdigit"] = ((a % 10) == (b % 10)) & (a != b)
    ks["decade"] = ((a // 10) == (b // 10)) & (a != b)
    for m in (3, 4, 7):
        ks[f"mod{m}"] = ((a % m) == (b % m)) & (a != b)
    out = []
    for k, v in ks.items():
        v = v.astype(float)
        np.fill_diagonal(v, 0.0)
        if v.sum() > 0:
            out.append((k, v))
    return out


def ar_pooled(C: np.ndarray, order: int, warm: int) -> np.ndarray:
    n_days, N = C.shape
    x = C - C.mean(axis=1, keepdims=True)
    S = np.zeros((n_days, N))
    for fit_at in range(max(warm, order + 10), n_days, REFIT_EVERY):
        rows_y = x[order:fit_at].ravel()
        X = np.stack([x[order - l : fit_at - l].ravel() for l in range(1, order + 1)], axis=1)
        phi = np.linalg.solve(X.T @ X + 1.0 * np.eye(order), X.T @ rows_y)
        for d in range(fit_at, min(fit_at + REFIT_EVERY, n_days)):
            S[d] = sum(phi[l - 1] * x[d - l] for l in range(1, order + 1))
    return S


def logistic_online(features: list[np.ndarray], Ib: np.ndarray, lam: float, warm: int) -> np.ndarray:
    n_days, N = Ib.shape
    F = np.stack([day_rank(f) - 0.5 for f in features], axis=2)  # days x N x k
    F = np.concatenate([np.ones((n_days, N, 1)), F], axis=2)
    S = np.zeros((n_days, N))
    lo = 50
    for fit_at in range(max(warm, lo + 50), n_days, REFIT_EVERY):
        X = F[lo:fit_at].reshape(-1, F.shape[2])
        y = Ib[lo:fit_at].ravel()
        w = np.zeros(F.shape[2])
        w[0] = np.log(max(y.mean(), 1e-6) / max(1 - y.mean(), 1e-6))
        reg = lam * np.eye(F.shape[2])
        reg[0, 0] = 0.0
        for _ in range(12):
            z = X @ w
            p = 1.0 / (1.0 + np.exp(-z))
            g = X.T @ (y - p) - reg @ w
            H = (X * (p * (1 - p))[:, None]).T @ X + reg
            step = np.linalg.solve(H, g)
            w += step
            if np.max(np.abs(step)) < 1e-8:
                break
        end = min(fit_at + REFIT_EVERY, n_days)
        S[fit_at:end] = F[fit_at:end] @ w
    return S


# ------------------------------------------------------------------- the bank


def bank(data: Data):
    """Generator (nume, familie, scor zile x numere); S[d] vede numai zilele < d."""
    N, Ib, C = data.N, data.Ib, data.C
    CS = cumsum0(C)
    last = shift(C)
    G = gaps(Ib)
    alltime = CS[:-1]

    for w in (5, 10, 20, 30, 50, 75, 100, 150, 200, 300, 500, 800, 10**9):
        S = window(CS, w)
        tag = "all" if w >= 10**9 else str(w)
        yield f"win{tag}", "frequency", S
        yield f"anti_win{tag}", "frequency", -S
    ew = {}
    for a in (0.005, 0.01, 0.02, 0.03, 0.05, 0.08, 0.12, 0.2, 0.3, 0.5):
        ew[a] = ewma_state(C, a)
        yield f"ewma{a}", "ewma", ew[a]
        yield f"anti_ewma{a}", "ewma", -ew[a]

    mean_gap = (np.arange(data.n_days)[:, None] + 1.0) / (alltime + 1.0)
    yield "gap", "gap", G
    yield "anti_gap", "gap", -G
    yield "gap_ratio", "gap", G / mean_gap
    yield "anti_gap_ratio", "gap", -G / mean_gap
    hz = hazard_pooled(Ib, G)
    yield "hazard", "gap", hz
    yield "anti_hazard", "gap", -hz

    for s, l in ((5, 50), (10, 50), (20, 100), (30, 200), (50, 300), (100, 500), (10, 200)):
        S = window(CS, s) / s - window(CS, l) / l
        yield f"mom{s}_{l}", "momentum", S
        yield f"anti_mom{s}_{l}", "momentum", -S

    mk = {}
    for lag in (1, 2, 3, 4, 7):
        mk[lag] = markov(Ib, lag)
        yield f"markov{lag}", "markov", mk[lag]
        yield f"anti_markov{lag}", "markov", -mk[lag]
    multi = mk[1] + mk[2] + mk[3]
    yield "markov123", "markov", multi
    yield "anti_markov123", "markov", -multi

    co_all = cooccurrence(Ib, None)
    co_ew = cooccurrence(Ib, 0.01)
    for name, S in (("cooc_all", co_all), ("cooc_ewma", co_ew)):
        yield name, "cooccurrence", S
        yield f"anti_{name}", "cooccurrence", -S

    e3 = ew[0.3]
    e02 = ew[0.02]
    king7 = None
    for lname, xy in layouts(N):
        for kname, K in grid_kernels(xy):
            for mname, base in (("last", last), ("ewma0.3", e3), ("ewma0.02", e02)):
                S = base @ K
                if lname == "row7" and kname == "king" and mname == "last":
                    king7 = S
                yield f"grid_{lname}_{kname}_{mname}", "grid", S
                yield f"anti_grid_{lname}_{kname}_{mname}", "grid", -S

    dist1 = None
    for kname, K in line_kernels(N):
        for mname, base in (("last", last), ("ewma0.3", e3)):
            S = base @ K
            if kname == "dist1" and mname == "last":
                dist1 = S
            yield f"line_{kname}_{mname}", "line", S
            yield f"anti_line_{kname}_{mname}", "line", -S

    if data.d > 1:
        PS, PD = pair_sum_diff(data)
        for name, X in (("pairsum", PS), ("pairdiff", PD)):
            S = shift(X)
            yield name, "pairs", S
            yield f"anti_{name}", "pairs", -S

    if data.P is not None:
        d = data.d
        for p in range(d):
            yield f"pos{p + 1}_ewma0.02", "order", ewma_state(data.P[:, p, :], 0.02)
        wts = np.array([(d - 1 - 2 * p) / (d - 1) for p in range(d)])
        early = np.tensordot(data.P, wts, axes=([1], [0]))
        S = ewma_state(early, 0.02)
        yield "early_ewma0.02", "order", S
        yield "anti_early_ewma0.02", "order", -S
        for k in (1, 2, 3):
            S = shift(data.P[:, :k, :].sum(axis=1))
            yield f"last_first{k}", "order", S
            yield f"anti_last_first{k}", "order", -S

    wd = pd.DatetimeIndex(data.day_dates).dayofweek.to_numpy()
    mo = pd.DatetimeIndex(data.day_dates).month.to_numpy() - 1
    wk = None
    for prior in (8.0, 30.0):
        S = conditional_freq(Ib, wd, 7, prior)
        if prior == 8.0:
            wk = S
        yield f"weekday_p{int(prior)}", "calendar", S
        yield f"month_p{int(prior)}", "calendar", conditional_freq(Ib, mo, 12, prior)

    for pname, vals in day_properties(data).items():
        if np.ptp(vals) == 0:
            continue
        key = online_tercile_key(vals)
        S = conditional_freq(Ib, key, 3, 4.0)
        yield f"cond_{pname}", "conditional", S
        yield f"anti_cond_{pname}", "conditional", -S

    for P in (2, 3, 4, 5, 6, 7, 10, 14, 21, 28):
        key = np.arange(data.n_days) % P
        S = conditional_freq(Ib, key, P, 4.0)
        yield f"period{P}", "periodic", S
        yield f"anti_period{P}", "periodic", -S

    for order in (1, 2, 5, 10, 20):
        yield f"ar{order}", "ar", ar_pooled(C, order, WARMUP_DAYS)

    feats = [window(CS, 50), window(CS, 200), alltime, e02, ew[0.12], G / mean_gap, mk[1], co_all, dist1, wk,
             window(CS, 20) / 20 - window(CS, 100) / 100]
    if king7 is not None:
        feats.append(king7)
    for lam in (1.0, 100.0):
        yield f"logit_l{int(lam)}", "learned", logistic_online(feats, Ib, lam, WARMUP_DAYS)

    def ravg(*mats):
        return sum(day_rank(m) for m in mats) / len(mats)

    yield "ens_ewma_gap_markov", "ensemble", ravg(e02, G, mk[1])
    yield "ens_all_cooc_grid", "ensemble", ravg(alltime, co_all, king7 if king7 is not None else dist1)
    yield "ens_hot", "ensemble", ravg(*(ew[a] for a in (0.05, 0.08, 0.12, 0.2)))
    yield "ens_cold", "ensemble", ravg(*(-ew[a] for a in (0.005, 0.01, 0.02, 0.03)))


# ---------------------------------------------------------------- evaluation


def hyp_mean_var(N: int, d: int, K: int) -> tuple[float, float]:
    mu = K * d / N
    var = K * d * (N - K) * (N - d) / (N * N * (N - 1))
    return mu, var


def hyp_tail(N: int, d: int, K: int, t: int) -> float:
    return sum(comb(K, h) * comb(N - K, d - h) for h in range(t, min(K, d) + 1)) / comb(N, d)


def evaluate(S_day: np.ndarray, data: Data, Ks) -> dict:
    """hits[K] (randuri) si tie[K] (numarul decide locul K)."""
    S = S_day[data.day]
    tb = cumsum0(data.C)[:-1][data.day]
    num = np.broadcast_to(np.arange(data.N), S.shape)
    order = np.lexsort((-num, -tb, -S), axis=1)
    r = np.arange(len(S))[:, None]
    present = np.zeros(S.shape, dtype=bool)
    present[r, data.draws - 1] = True
    cum = np.cumsum(present[r, order], axis=1)
    out = {}
    for K in Ks:
        a, b = order[:, K - 1], order[:, K]
        rr = np.arange(len(S))
        tie = (S[rr, a] == S[rr, b]) & (tb[rr, a] == tb[rr, b])
        out[K] = (cum[:, K - 1].astype(np.int16), tie)
    return out


def masks(data: Data) -> dict[str, np.ndarray]:
    cut = WARMUP_DAYS + int(DEV_FRACTION * (data.n_days - WARMUP_DAYS))
    mid = WARMUP_DAYS + (cut - WARMUP_DAYS) // 2
    day = data.day
    return {
        "dev": (day >= WARMUP_DAYS) & (day < cut),
        "dev1": (day >= WARMUP_DAYS) & (day < mid),
        "dev2": (day >= mid) & (day < cut),
        "conf": day >= cut,
        "all": day >= WARMUP_DAYS,
    }


def stats(hits: np.ndarray, mask: np.ndarray, N: int, d: int, K: int, target: int) -> dict:
    n = int(mask.sum())
    mu, var = hyp_mean_var(N, d, K)
    p = hyp_tail(N, d, K, target)
    H = int(hits[mask].sum())
    E = int((hits[mask] >= target).sum())
    return {
        "n": n,
        "hits": H,
        "excess_hits": H - n * mu,
        "z_hits": (H - n * mu) / sqrt(n * var) if n else 0.0,
        "events": E,
        "rate": E / n if n else 0.0,
        "p0": p,
        "z_event": (E - n * p) / sqrt(n * p * (1 - p)) if n else 0.0,
    }


def screen(data: Data, Ks, target: int, keep_hits: bool = False):
    m = masks(data)
    rows = []
    hits_store = {}
    for name, family, S in bank(data):
        if not np.all(np.isfinite(S)):
            continue
        ev = evaluate(S, data, Ks)
        for K, (hits, tie) in ev.items():
            tie_rate = float(tie[m["dev"]].mean())
            dev = stats(hits, m["dev"], data.N, data.d, K, target)
            h1 = stats(hits, m["dev1"], data.N, data.d, K, target)
            h2 = stats(hits, m["dev2"], data.N, data.d, K, target)
            rows.append(
                {
                    "name": name,
                    "family": family,
                    "K": K,
                    "tie_rate": tie_rate,
                    "gated": tie_rate > TIE_GATE,
                    "stable": h1["excess_hits"] > 0 and h2["excess_hits"] > 0,
                    "dev_z_hits": dev["z_hits"],
                    "dev_z_event": dev["z_event"],
                    "dev_rate": dev["rate"],
                    "dev_p0": dev["p0"],
                    "dev_n": dev["n"],
                }
            )
            if keep_hits:
                hits_store[(name, K)] = hits
    return pd.DataFrame(rows), hits_store, m


def _null_one(args):
    game, seed = args
    cfg = GAMES[game]
    base = load(cfg["path"], cfg["cols"], cfg["N"])
    syn = synthetic(base, np.random.default_rng(seed))
    table, _, _ = screen(syn, cfg["Ks"], cfg["target"])
    ok = table[~table["gated"]]
    st = ok[ok["stable"]]
    return {
        "max_z_hits": float(st["dev_z_hits"].max()) if len(st) else float("-inf"),
        "max_z_event": float(st["dev_z_event"].max()) if len(st) else float("-inf"),
        "max_z_hits_any": float(ok["dev_z_hits"].max()),
        "mean_z_hits": float(ok["dev_z_hits"].mean()),
        "sd_z_hits": float(ok["dev_z_hits"].std()),
        "n_tests": int(len(ok)),
    }


def leak_check(data: Data) -> int:
    """Schimba extragerile unei zile; scorurile pana la acea zi inclusiv trebuie sa ramana identice."""
    d0 = data.n_days // 2
    draws = data.draws.copy()
    rows = np.flatnonzero(data.day == d0)
    rng = np.random.default_rng(7)
    for r in rows:
        draws[r] = rng.permutation(data.N)[: data.d] + 1
    alt = Data(draws, data.day, data.day_dates, data.N, data.ordered)
    checked = 0
    for (n1, _f1, S1), (n2, _f2, S2) in zip(bank(data), bank(alt)):
        assert n1 == n2
        if not np.array_equal(S1[: d0 + 1], S2[: d0 + 1]):
            raise AssertionError(f"leak in {n1}")
        checked += 1
    return checked


def holm(pvals: list[float]) -> list[float]:
    m = len(pvals)
    order = np.argsort(pvals)
    adj = np.empty(m)
    running = 0.0
    for rank, idx in enumerate(order):
        running = max(running, (m - rank) * pvals[idx])
        adj[idx] = min(1.0, running)
    return [float(x) for x in adj]


def select(table: pd.DataFrame) -> list[dict]:
    ok = table[(~table["gated"]) & (table["stable"])]
    picked: list[dict] = []
    names: set[str] = set()
    for col, n in (("dev_z_hits", 3), ("dev_z_event", 2)):
        taken = 0
        for _, r in ok.sort_values([col, "name", "K"], ascending=[False, True, True]).iterrows():
            if r["name"] in names:
                continue
            picked.append({"name": r["name"], "K": int(r["K"]), "family": r["family"], "by": col,
                           "dev_z_hits": float(r["dev_z_hits"]), "dev_z_event": float(r["dev_z_event"]),
                           "dev_rate": float(r["dev_rate"]), "dev_p0": float(r["dev_p0"])})
            names.add(r["name"])
            taken += 1
            if taken == n:
                break
    return picked


def main() -> None:
    t0 = time.time()
    games = sys.argv[1:] or list(GAMES)
    report: dict = {"protocol": __doc__, "games": {}}
    real = {}
    for game in games:
        cfg = GAMES[game]
        data = load(cfg["path"], cfg["cols"], cfg["N"])
        if game == "ro_649":
            print(f"[leak] {leak_check(data)} candidates verified, no future data", flush=True)
        table, hits, m = screen(data, cfg["Ks"], cfg["target"], keep_hits=True)
        real[game] = (data, table, hits, m)
        ok = table[~table["gated"]]
        print(
            f"[{game}] rows={len(data.draws)} days={data.n_days} ordered={data.ordered} "
            f"candidates={table['name'].nunique()} tests={len(table)} gated={int(table['gated'].sum())} "
            f"max z_hits={ok['dev_z_hits'].max():.2f} ({time.time() - t0:.0f}s)",
            flush=True,
        )

    jobs = [(g, SEED + 1000 * i + j) for i, g in enumerate(games) for j in range(N_NULL)]
    null: dict[str, list] = {g: [] for g in games}
    with ProcessPoolExecutor(max_workers=24) as ex:
        for (g, _s), res in zip(jobs, ex.map(_null_one, jobs, chunksize=2)):
            null[g].append(res)
    print(f"[null] {len(jobs)} synthetic screens ({time.time() - t0:.0f}s)", flush=True)

    ro_tests = []
    for game in games:
        cfg = GAMES[game]
        data, table, hits, m = real[game]
        ok = table[~table["gated"]]
        st = ok[ok["stable"]]
        best_h = float(st["dev_z_hits"].max())
        best_e = float(st["dev_z_event"].max())
        nh = np.array([r["max_z_hits"] for r in null[game]])
        ne = np.array([r["max_z_event"] for r in null[game]])
        picked = select(table)
        conf = []
        for c in picked:
            h = hits[(c["name"], c["K"])]
            s = stats(h, m["conf"], data.N, data.d, c["K"], cfg["target"])
            p_event = float(binomtest(s["events"], s["n"], s["p0"], alternative="greater").pvalue)
            p_hits = float(norm.sf(s["z_hits"]))
            rec = {**c, "confirm": s, "p_event": p_event, "p_hits": p_hits}
            conf.append(rec)
            ro_tests.append((game, rec))
        report["games"][game] = {
            "rows": int(len(data.draws)),
            "days": int(data.n_days),
            "ordered": bool(data.ordered),
            "candidates": int(table["name"].nunique()),
            "tests": int(len(table)),
            "gated": int(table["gated"].sum()),
            "stable": int(len(st)),
            "families": table.groupby("family")["name"].nunique().to_dict(),
            "dev_best_z_hits": best_h,
            "dev_best_z_event": best_e,
            "null_max_z_hits_q95": float(np.quantile(nh, 0.95)),
            "null_max_z_event_q95": float(np.quantile(ne, 0.95)),
            "p_search_hits": float((1 + np.sum(nh >= best_h)) / (len(nh) + 1)),
            "p_search_event": float((1 + np.sum(ne >= best_e)) / (len(ne) + 1)),
            "null_mean_z": float(np.mean([r["mean_z_hits"] for r in null[game]])),
            "null_sd_z": float(np.mean([r["sd_z_hits"] for r in null[game]])),
            "real_mean_z": float(ok["dev_z_hits"].mean()),
            "real_sd_z": float(ok["dev_z_hits"].std()),
            "top_dev": ok.sort_values("dev_z_hits", ascending=False).head(15).to_dict(orient="records"),
            "selected": conf,
        }
        print(
            f"[{game}] dev best z_hits={best_h:.2f} (null q95 {np.quantile(nh, 0.95):.2f}, "
            f"p={report['games'][game]['p_search_hits']:.3f}); best z_event={best_e:.2f} "
            f"(null q95 {np.quantile(ne, 0.95):.2f})",
            flush=True,
        )

    adj_event = holm([r["p_event"] for _g, r in ro_tests])
    adj_hits = holm([r["p_hits"] for _g, r in ro_tests])
    for (game, rec), ae, ah in zip(ro_tests, adj_event, adj_hits):
        rec["holm_event"] = ae
        rec["holm_hits"] = ah

    for game in games:
        cfg = GAMES[game]
        selected = report["games"][game]["selected"]
        ext_stats: dict[str, list] = {rec["name"]: [] for rec in selected}
        for label, path, cols, N in EXTERNAL.get(game, []):
            ext = load(path, cols, N)
            wanted = {rec["name"]: rec["K"] for rec in selected}
            found = set()
            for name, _f, S in bank(ext):
                if name not in wanted:
                    continue
                found.add(name)
                h, _tie = evaluate(S, ext, (wanted[name],))[wanted[name]]
                s = stats(h, ext.day >= WARMUP_DAYS, ext.N, ext.d, wanted[name], cfg["target"])
                ext_stats[name].append({"lottery": label, **s})
            for name in set(wanted) - found:
                ext_stats[name].append({"lottery": label, "status": "formula indisponibila (fara ordinea bilelor)"})
        for rec in selected:
            ext_rows = ext_stats[rec["name"]]
            tot_n = sum(r["n"] for r in ext_rows if "n" in r)
            tot_e = sum(r["events"] for r in ext_rows if "n" in r)
            tot_p = sum(r["n"] * r["p0"] for r in ext_rows if "n" in r)
            if tot_n:
                z = (tot_e - tot_p) / sqrt(sum(r["n"] * r["p0"] * (1 - r["p0"]) for r in ext_rows if "n" in r))
                rec["external"] = {"rows": ext_rows, "n": tot_n, "events": tot_e, "expected": tot_p,
                                   "z": z, "p": float(norm.sf(z))}
            rec["promote"] = bool(
                rec["holm_event"] < 0.05
                and rec["confirm"]["events"] > rec["confirm"]["n"] * rec["confirm"]["p0"]
                and (("external" not in rec) or rec["external"]["p"] < 0.05)
            )
            ext_txt = f" ext p={rec['external']['p']:.3f}" if "external" in rec else ""
            print(
                f"[{game}] {rec['name']} K={rec['K']} conf rate={100 * rec['confirm']['rate']:.2f}% "
                f"vs {100 * rec['confirm']['p0']:.2f}% p={rec['p_event']:.3f} holm={rec['holm_event']:.3f} "
                f"z_hits={rec['confirm']['z_hits']:.2f}{ext_txt} promote={rec['promote']}",
                flush=True,
            )

    report["promoted"] = [
        {"game": g, "name": r["name"], "K": r["K"]}
        for g in games
        for r in report["games"][g]["selected"]
        if r["promote"]
    ]
    report["runtime_sec"] = time.time() - t0
    dest = ROOT / "scripts" / "analysis" / "prediction_screen_2026-10-05.json"
    dest.write_text(json.dumps(report, indent=1, default=float), encoding="utf-8")
    print(f"done {time.time() - t0:.0f}s promoted={report['promoted']}", flush=True)


if __name__ == "__main__":
    main()
