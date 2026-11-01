"""Ecran de metode noi orientate pe hituri 4+ si 5+, 2026-11-01. Protocol fixat inainte de rulare.

Intrebarea utilizatorului: exista metode noi care pun in pool mai multe numere
extrase, masurat la pragurile 4+ si 5+ (ecranele din 2026-10-05/09/10 au testat
mai ales 3+/4+)?

Date. Romania: 6/49, 5/40 (sase extrase), Joker Urna 1 — ca in
prediction_screen_2026-10-05.py. Scorul unei zile vede numai zilele ANTERIOARE;
extragerile din aceeasi zi primesc acelasi scor. 200 de zile warmup, primele 70%
din rest dezvoltare, ultimele 30% confirmare.

Celule. 6/49, 5/40 si Joker Urna 1 pe poolurile (11, 16) la tintele 4+ si 5+.
Liderul celulei = castigatorul deciziei din bench_results/folds.csv la tinta 4
(decizia de productie; tinta 5+ nu exista in decizie, se compara cu acelasi
lider), rulat pe aceleasi extrageri cu call_method + rank_by_score + rezerva
`frequency` la scor inutilizabil, ca in productie.

Candidati. Familii NETestate pana acum (nu sunt in registry si nici in bancile
din 2026-10-05/09/10), toate echivariante la redenumirea numerelor, fara filtre
structurale, deterministe:
  T1 triple completion — suma lifturilor (sau a numarului brut de aparitii)
     triplelor observate care il contin pe n, pe fereastra L (F08 din 10-10 a
     acoperit doar perechi)
  T2 quad completion — aceeasi idee pe cvadruplele observate
  T10 triple-hot — liftul triplelor care ating ultimul joc (prin perechi cu
     numerele din ultimul joc)
  T3 NMF — factorizare nenegativa multiplicativa a matricei-indicator pe
     fereastra, prognoza = ultima componenta W @ H (DMD din registry e pe SVD)
  T4 cluster-conditional — k-means pe liniile ferestrei, frecventa fiecarui
     numar in clusterul zilei precedente (B3 din 10-09 era pe era chi^2)
  T6 decayed pair lift — liftul perechilor cu dezintegrare exponentiala
     (halflife h); scorul lui n = suma lifturilor cu numerele ultimului joc
Fiecare familie scalara apare si cu semn schimbat (anti_).

Nul al cautarii: N_NULL istorii sintetice uniforme cu aceeasi structura de
zile; pe fiecare se reface intreaga banca noua; maximul z de dezvoltare per
celula da p-ul cautarii.

Selectie inghetata pe dezvoltare, per celula: candidati fara dependenta de
egalitati (locul K decis de numar in >50% din randuri) si stabili (exces de
hituri pe ambele jumatati ale dezvoltarii); primii 2 dupa z al evenimentului
si primul dupa z al hiturilor (nume distincte).

Confirmare pe ultimele 30% romanesti, pentru selectia inghetata:
  (a) „depaseste liderul": McNemar exact unilateral pe evenimentul tinta;
  (b) fata de hazard: binomial exact unilateral fata de rata hipergeometrica.
  Holm separat pe (a) si (b), peste toti candidatii inghetati.
  (c) Replicare externa: 6/49 -> cele 6 arhive straine 6/49; Joker Urna 1 ->
  6/45 (Austria, Belgia, Ungaria) si 5/50 (EuroMillions); aceeasi formula,
  acelasi K si aceeasi tinta, z pe suma evenimentelor fata de suma asteptata.
Promovare numai cu Holm(a) < 0.05, Holm(b) < 0.05 si, unde exista, p(c) < 0.05.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import os
import sys
import time
from collections import deque
from concurrent.futures import ProcessPoolExecutor
from itertools import combinations
from math import sqrt
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import binom, binomtest, norm

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "prediction_screen_20261005", HERE / "prediction_screen_2026-10-05.py"
)
ps = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ps)

ROOT = ps.ROOT
sys.path.insert(0, str(ROOT))

SEED = 20261101
N_NULL = int(os.environ.get("HIT5_NULL", 100))
WORKERS = int(os.environ.get("HIT5_WORKERS", 16))
OUT = Path(os.environ.get("HIT5_OUT", HERE / "hit5plus_screen_2026-11-01.json"))

GAMES = {
    "ro_649": {**ps.GAMES["ro_649"], "Ks": (11, 16), "targets": (4, 5), "bench": "loto_6_49"},
    "ro_540": {**ps.GAMES["ro_540"], "Ks": (11, 16), "targets": (4, 5), "bench": "loto_5_40"},
    "ro_joker1": {**ps.GAMES["ro_joker1"], "Ks": (11, 16), "targets": (4, 5), "bench": "joker_urna1"},
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


def _subset_scores(Ib: np.ndarray, N: int, d_draw: int, order: int, L: int | None, use_lift: bool):
    """S[d][n] = suma (lift sau brut) a subseturilor de `order` observate care
    contin n, din ultimele L zile (toate, daca L is None). S[d] vede zilele < d."""
    n_days = Ib.shape[0]
    S = np.zeros((n_days, N))
    counts: dict[int, int] = {}
    sums = np.zeros(N)
    hist: deque = deque()
    W = n_days if L is None else L
    exp = max(W, 1) * (d_draw / N) ** order
    inc = 1.0 / exp if use_lift else 1.0
    for d in range(n_days):
        S[d] = sums
        row = np.flatnonzero(Ib[d])
        keys = []
        for combo in combinations(row.tolist(), order):
            key = 0
            for x in combo:
                key = key * N + x
            keys.append(key)
            counts[key] = counts.get(key, 0) + 1
            for n in combo:
                sums[n] += inc
        hist.append((d, keys))
        if L is not None:
            while hist and hist[0][0] <= d - L:
                _dd, ks = hist.popleft()
                for key in ks:
                    counts[key] -= 1
                    x = key
                    combo = []
                    for _ in range(order):
                        combo.append(x % N)
                        x //= N
                    for n in combo:
                        sums[n] -= inc
    return S


def _triple_hot_scores(Ib: np.ndarray, N: int, d_draw: int, L: int | None):
    """S[d][n] = suma lifturilor triplelor care contin n SI ating ultimul joc
    (via perechile (n, m) cu m din ultimul joc). S[d] vede zilele < d."""
    n_days = Ib.shape[0]
    S = np.zeros((n_days, N))
    pairsum: dict[tuple[int, int], float] = {}
    hist: deque = deque()
    W = n_days if L is None else L
    exp = max(W, 1) * (d_draw / N) ** 3
    inc = 1.0 / exp
    for d in range(n_days):
        hot = Ib[d - 1] if d > 0 else np.zeros(N)
        if hot.any():
            ms = np.flatnonzero(hot)
            for n in range(N):
                s = 0.0
                for m in ms:
                    s += pairsum.get((min(n, m), max(n, m)), 0.0)
                S[d, n] = s
        else:
            S[d] = 0.0
        row = np.flatnonzero(Ib[d])
        keys = []
        for combo in combinations(row.tolist(), 3):
            key = (combo[0] * N + combo[1]) * N + combo[2]
            keys.append(key)
            for a, b in combinations(combo, 2):
                pk = (min(a, b), max(a, b))
                pairsum[pk] = pairsum.get(pk, 0.0) + inc
        hist.append((d, keys))
        if L is not None:
            while hist and hist[0][0] <= d - L:
                _dd, ks = hist.popleft()
                for key in ks:
                    x = key
                    combo = []
                    for _ in range(3):
                        combo.append(x % N)
                        x //= N
                    for a, b in combinations(combo, 2):
                        pk = (min(a, b), max(a, b))
                        pairsum[pk] = pairsum.get(pk, 0.0) - inc
    return S


def _nmf_forecast(Ibw: np.ndarray, r: int, iters: int = 30, seed: int = 0) -> np.ndarray:
    """Init dependent doar de date (echivariant la permutarea etichetelor):
    liniile lui W / coloanele lui H pornesc din sumele pe axe, cu o variatie
    mica pe componenta (index de componenta, nu de eticheta)."""
    L, N = Ibw.shape
    comp = 1.0 + 0.01 * np.arange(r)
    W = Ibw.sum(axis=1, keepdims=True) * comp[None, :] + 0.1
    H = Ibw.sum(axis=0)[None, :] * comp[:, None] + 0.1
    for _ in range(iters):
        W *= (Ibw @ H.T) / (W @ (H @ H.T) + 1e-9)
        H *= (W.T @ Ibw) / ((W.T @ W) @ H + 1e-9)
    return W[-1] @ H


def _nmf_scores(Ib: np.ndarray, N: int, L: int | None, r: int, every: int = 10):
    n_days = Ib.shape[0]
    S = np.zeros((n_days, N))
    cur = np.zeros(N)
    for d0 in range(0, n_days, every):
        lo = max(0, d0 - L) if L is not None else 0
        if d0 - lo >= 50:
            cur = _nmf_forecast(Ib[lo:d0], r, seed=SEED + r)
        for d in range(d0, min(d0 + every, n_days)):
            S[d] = cur
    return S


def _kmeans(X: np.ndarray, k: int, iters: int = 20, seed: int = 0):
    rng = np.random.default_rng(seed)
    C = X[rng.choice(len(X), k, replace=False)].copy()
    lab = np.zeros(len(X), dtype=int)
    for _ in range(iters):
        d2 = ((X[:, None, :] - C[None, :, :]) ** 2).sum(-1)
        lab = d2.argmin(1)
        for j in range(k):
            if (lab == j).any():
                C[j] = X[lab == j].mean(0)
    return lab


def _cluster_scores(Ib: np.ndarray, N: int, L: int | None, k: int, every: int = 20):
    """S[d][n] = frecventa lui n in clusterul (k-means) al ultimei zile, pe
    fereastra L. S[d] vede zilele < d."""
    n_days = Ib.shape[0]
    S = np.zeros((n_days, N))
    cur = np.zeros(N)
    for d0 in range(0, n_days, every):
        lo = max(0, d0 - L) if L is not None else 0
        if d0 - lo >= 50:
            lab = _kmeans(Ib[lo:d0], k, seed=SEED + k)
            last_cluster = lab[-1]
            sel = lab == last_cluster
            if sel.sum() >= 5:
                cur = Ib[lo:d0][sel].mean(0)
        for d in range(d0, min(d0 + every, n_days)):
            S[d] = cur
    return S


def _decayed_pair_lift_scores(Ib: np.ndarray, N: int, halflife: float):
    """S[d][n] = suma lifturilor perechilor (n, m) cu m din ultimul joc, cu
    dezintegrare exponentiala halflife. S[d] vede zilele < d."""
    n_days = Ib.shape[0]
    S = np.zeros((n_days, N))
    P = np.zeros((N, N))
    rn = np.zeros(N)
    rd = 0.0
    f = float(np.exp(-1.0 / halflife))
    eps = 1e-9
    for d in range(n_days):
        if d > 0:
            lift = P * rd / (np.outer(rn, rn) + eps)
            S[d] = lift @ Ib[d - 1]
        P *= f
        rn *= f
        rd = rd * f + 1.0
        P += np.outer(Ib[d], Ib[d])
        rn += Ib[d]
    return S


# ------------------------------------------------------------------- the bank


def bank(data):
    """Generator (nume, familie, scor zile x numere); S[d] vede numai zilele < d."""
    N, Ib = data.N, data.Ib
    d_draw = data.d

    def both(name, fam, S):
        yield name, fam, S
        yield f"anti_{name}", fam, -S

    for L in (200, 500, None):
        for use_lift in (True, False):
            tag = "lift" if use_lift else "raw"
            yield from both(
                f"triple_L{'all' if L is None else L}_{tag}",
                "T1_triple",
                _subset_scores(Ib, N, d_draw, 3, L, use_lift),
            )
    for L in (500, None):
        yield from both(
            f"quad_L{'all' if L is None else L}",
            "T2_quad",
            _subset_scores(Ib, N, d_draw, 4, L, False),
        )
    for L in (500, None):
        yield from both(
            f"triplehot_L{'all' if L is None else L}",
            "T10_triple_hot",
            _triple_hot_scores(Ib, N, d_draw, L),
        )
    for r in (3, 6):
        for L in (300, None):
            yield from both(
                f"nmf_r{r}_L{'all' if L is None else L}",
                "T3_nmf",
                _nmf_scores(Ib, N, L, r),
            )
    for k in (3, 5):
        for L in (500, None):
            yield from both(
                f"cluster_k{k}_L{'all' if L is None else L}",
                "T4_cluster",
                _cluster_scores(Ib, N, L, k),
            )
    for h in (10.0, 30.0, 100.0):
        yield from both(
            f"pairlift_h{int(h)}",
            "T6_decayed_pairlift",
            _decayed_pair_lift_scores(Ib, N, h),
        )


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
        plan = (("dev_z_event", 2), ("dev_z_hits", 1))
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


def leaders(game: str) -> dict[int, str]:
    """Castigatorul deciziei per pool la tinta 4 (decizia de productie)."""
    import loto_enterprise.benchmark.decision as dec

    folds = pd.read_csv(ROOT / "bench_results" / "folds.csv")
    if "failed" not in folds.columns:
        folds["failed"] = False
    out = {}
    for K in GAMES[game]["Ks"]:
        dec.BENCH_HIT_TARGET = 4
        cfg = dec.decide_optimal_config_for_pool(
            folds, GAMES[game]["bench"], pool_size=K, draw_n=len(GAMES[game]["cols"]), max_num=GAMES[game]["N"]
        )
        members = [m["method"] for m in (cfg.get("ensemble") or [])]
        out[K] = members[0] if members else "frequency"
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
                lh = lead_hits[lead[s["K"]]][s["K"]]
                conf = m["conf"]
                new_ev = hits[conf] >= s["target"]
                lead_ev = lh[conf] >= s["target"]
                b, c, p_mc = mcnemar_greater(new_ev, lead_ev)
                p0 = tail(data.N, data.d, s["K"], s["target"])
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
                        "p0": tail(data.N, data.d, K, t),
                    }
                    for K, v in lead.items() for t in GAMES[game]["targets"]
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
