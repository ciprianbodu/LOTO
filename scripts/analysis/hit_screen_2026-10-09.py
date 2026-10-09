"""Ecran de optimizare a ratei de hit, 2026-10-09. Protocol fixat inainte de rulare.

Intrebarea: exista vreo metoda care pune in pool mai multe numere extrase decat
un pool aleator de aceeasi marime (mai ales pool 16, tinta 4+)? Ecranul din
2026-10-05 (prediction_screen_2026-10-05.py, 491 de scoreri in 16 familii) a
raspuns nu. Aici se testeaza numai ce nu s-a testat acolo.

Partea A. Bateria de aleatorism, pe 14 serii: Romania 6/49, 5/40, Joker Urna 1,
Joker Urna 2 si cele 10 istorice straine din _ISTORIC/externe. Fiecare statistica
se compara cu M = 2000 de istorii sintetice iid, cu aceeasi structura de zile si
aceeasi marime de extragere:
  A1 frecventa numerelor (chi^2);
  A2 deriva: prima jumatate fata de a doua (chi^2 2xN);
  A3 numar x ziua saptamanii (zilele cu cel putin 100 de extrageri);
  A4 numar x pozitia bilei (numai arhivele cu ordinea extragerii);
  A5 repetari fata de extragerea cu 1, 2, 3 randuri inainte (bilateral);
  A6 perechi (chi^2 pe cele C(N,2) perechi);
  A7 serii pe fiecare numar, insumate (bilateral);
  A8 Romania: numere comune intre jocurile trase in aceeasi zi si intre un joc si
     extragerea anterioara a celuilalt (bilateral).
  p = (1 + #sintetice cel putin la fel de extreme) / (M + 1); Holm si
  Benjamini-Hochberg pe toata bateria.
  A9 Putere: un pool de 16 numere, fiecare cu sansa de extragere marita cu
  factorul (1 + e) (extragere ponderata fara intoarcere). Ce rata 4+ ar avea
  pool-ul si cu ce putere l-ar detecta A1 pe numarul real de extrageri.

Partea B. Familii noi de scoreri, pe protocolul din 2026-10-05: scorul unei zile
vede numai zilele anterioare, 200 de zile warmup, primele 70% din rest dezvoltare,
ultimele 30% confirmare, poarta de egalitati (>50% din randuri), nul al cautarii
pe N_NULL = 200 de istorii sintetice pe care se reface tot ecranul nou.
  B1 Intre jocuri (numai Romania): ultima extragere, EWMA (0.02, 0.1, 0.3) si
     ferestrele de 50 si 200 de zile ale celorlalte jocuri romanesti, numai din
     zilele anterioare zilei tinta; numarul n al sursei trece pe numarul n al
     tintei; si opusul fiecaruia.
  B2 Urmaritorul liderului (FTL): in fiecare zi, pool-ul scorerului cu cele mai
     multe hituri in ultimele L zile (50, 100, 200, 400) sau cu discount (0.99,
     0.995), ales dintre frecvente si EWMA ("freq", 52) sau dintre toti scorerii
     ecranului din 2026-10-05 ("bank", ~490). Egalitatea merge la primul din lista.
  B3 Frecventa pe era curenta: la fiecare 10 zile, chi^2 intre ultimele W zile si
     cele 4W de dinainte (W = 50, 100, 200); sub pragul alpha (0.01, 0.001) incepe
     o era noua, iar scorul numara aparitiile de la inceputul erei; si opusul.
  Pool-uri: 6/49 (6, 11, 16), 5/40 (8, 11, 16), Joker Urna 1 (8, 11, 16),
  Urna 2 (1). Evenimentul: 4+ la pool >= 11, 3+ sub 11; 5/40 mereu 4+; Urna 2
  numarul exact.
  Selectie inghetata pe dezvoltare, per joc: primii 3 stabili dupa z hituri,
  primii 2 dupa z eveniment si cel mai bun stabil la pool 16 dupa z eveniment.
  Confirmare: ultimele 30% romanesti, z unilateral pe hituri si binomial exact pe
  eveniment, Holm pe toate testele romanesti. Replicare externa pentru B2 si B3
  (B1 nu are istorice paralele): aceeasi formula si acelasi pool pe 6/49 straine,
  respectiv 6/45 si 5/50 pentru Urna 1. Promovare numai cu Holm < 0.05 pe hituri
  in Romania SI p < 0.05 pe hituri extern, unde exista.

Partea C. Plafonul retrospectiv: pool-ul celor mai frecvente 16 numere pe TOT
istoricul (privind inainte) si rata lui 4+ pe acelasi istoric, fata de acelasi
calcul pe 2000 de istorii sintetice.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from math import comb, sqrt
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import binom, binomtest, chi2, norm

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "prediction_screen_20261005", HERE / "prediction_screen_2026-10-05.py"
)
ps = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ps)

SEED = 20261009
M_BATTERY = int(os.environ.get("HIT_SCREEN_M", 2000))
N_NULL = int(os.environ.get("HIT_SCREEN_NULL", 200))
WORKERS = int(os.environ.get("HIT_SCREEN_WORKERS", 4))
EPS_GRID = (0.05, 0.10, 0.20, 0.35, 0.50)

GAMES = {
    "ro_649": {**ps.GAMES["ro_649"], "Ks": (6, 11, 16)},
    "ro_540": {**ps.GAMES["ro_540"], "Ks": (8, 11, 16)},
    "ro_joker1": {**ps.GAMES["ro_joker1"], "Ks": (8, 11, 16)},
    "ro_joker2": {**ps.GAMES["ro_joker2"], "Ks": (1,)},
}
SOURCES = {
    "ro_649": ("ro_540", "ro_joker1"),
    "ro_540": ("ro_649", "ro_joker1"),
    "ro_joker1": ("ro_649", "ro_540"),
    "ro_joker2": ("ro_649", "ro_540", "ro_joker1"),
}
SHORT = {"ro_649": "649", "ro_540": "540", "ro_joker1": "jk1"}
EXTERNAL = {
    "ro_649": ps.EXTERNAL["ro_649"],
    "ro_joker1": ps.EXTERNAL["ro_joker1"],
}


def target_for(game: str, K: int) -> int:
    if game == "ro_joker2":
        return 1
    if game == "ro_540":
        return 4
    return 4 if K >= 11 else 3


def load_game(game: str):
    cfg = GAMES[game]
    return ps.load(cfg["path"], cfg["cols"], cfg["N"])


def all_series() -> list[tuple[str, object]]:
    out = [(g, load_game(g)) for g in GAMES]
    seen = set()
    for rows in EXTERNAL.values():
        for label, path, cols, N in rows:
            if label not in seen:
                seen.add(label)
                out.append((label, ps.load(path, cols, N)))
    return out


# ----------------------------------------------------------------- part A


def fast_draws(rows: int, N: int, d: int, rng: np.random.Generator) -> np.ndarray:
    return np.argsort(rng.random((rows, N)), axis=1)[:, :d] + 1


def indicator(draws: np.ndarray, N: int) -> np.ndarray:
    rows = len(draws)
    I = np.zeros((rows, N), dtype=np.float32)
    I[np.arange(rows)[:, None], draws - 1] = 1.0
    return I


def contingency_chi2(T: np.ndarray) -> float:
    T = T.astype(float)
    E = T.sum(1, keepdims=True) * T.sum(0, keepdims=True) / T.sum()
    ok = E > 0
    return float((((T - E) ** 2)[ok] / E[ok]).sum())


TWO_SIDED = ("repeat_lag1", "repeat_lag2", "repeat_lag3", "runs")


def battery_stats(draws: np.ndarray, N: int, ordered: bool, wk: np.ndarray) -> dict[str, float]:
    rows, d = draws.shape
    I = indicator(draws, N)
    c = I.sum(0)
    e = rows * d / N
    out = {"freq_chi2": float(((c - e) ** 2).sum() / e)}
    h = rows // 2
    out["drift_chi2"] = contingency_chi2(np.vstack([I[:h].sum(0), I[h:].sum(0)]))
    keys = [k for k in np.unique(wk) if (wk == k).sum() >= 100]
    if len(keys) >= 2:
        out["weekday_chi2"] = contingency_chi2(np.vstack([I[wk == k].sum(0) for k in keys]))
    if ordered and d > 1:
        P = np.zeros((d, N))
        for p in range(d):
            np.add.at(P[p], draws[:, p] - 1, 1.0)
        out["position_chi2"] = contingency_chi2(P)
    for lag in (1, 2, 3):
        out[f"repeat_lag{lag}"] = float((I[lag:] * I[:-lag]).sum())
    if d > 1:
        pc = (I.T @ I)[np.triu_indices(N, 1)]
        ep = rows * d * (d - 1) / (N * (N - 1))
        out["pair_chi2"] = float(((pc - ep) ** 2).sum() / ep)
    out["runs"] = float(((I[1:] != I[:-1]).sum(0) + 1).sum())
    return out


def _mc_pvalues(real: dict, sims: list[dict]) -> dict:
    res = {}
    for k, v in real.items():
        arr = np.array([s[k] for s in sims])
        mu, sd = float(arr.mean()), float(arr.std())
        if k in TWO_SIDED or k.startswith("cross_"):
            extreme = np.abs(arr - mu) >= abs(v - mu)
        else:
            extreme = arr >= v
        res[k] = {
            "value": float(v),
            "null_mean": mu,
            "null_sd": sd,
            "z": (v - mu) / sd if sd > 0 else 0.0,
            "p": float((1 + extreme.sum()) / (len(arr) + 1)),
        }
    return res


def _battery_one(args):
    label, seed = args
    data = dict(all_series())[label]
    wk = pd.DatetimeIndex(data.day_dates[data.day]).weekday.to_numpy()
    real = battery_stats(data.draws, data.N, data.ordered, wk)
    rng = np.random.default_rng(seed)
    rows, d = data.draws.shape
    sims = [battery_stats(fast_draws(rows, data.N, d, rng), data.N, data.ordered, wk) for _ in range(M_BATTERY)]
    return label, {"rows": rows, "N": data.N, "d": d, "ordered": bool(data.ordered), "tests": _mc_pvalues(real, sims)}


def day_indicator(draws: np.ndarray, day: np.ndarray, n_days: int, N: int) -> np.ndarray:
    Ib = np.zeros((n_days, N), dtype=np.float32)
    Ib[np.repeat(day, draws.shape[1]), draws.ravel() - 1] = 1.0
    return Ib


def cross_stats(A, IbA, B, IbB) -> dict[str, float]:
    m = min(A.N, B.N)
    common, ia, ib = np.intersect1d(A.day_dates, B.day_dates, return_indices=True)
    a, b = IbA[ia, :m], IbB[ib, :m]
    return {
        "cross_same_day": float((a * b).sum()),
        "cross_prev_a_after_b": float((a[1:] * b[:-1]).sum()),
        "cross_prev_b_after_a": float((b[1:] * a[:-1]).sum()),
        "common_days": float(len(common)),
    }


def _cross_one(args):
    ga, gb, seed = args
    A, B = load_game(ga), load_game(gb)
    real = cross_stats(A, A.Ib, B, B.Ib)
    rng = np.random.default_rng(seed)
    sims = []
    for _ in range(M_BATTERY):
        sa = day_indicator(fast_draws(len(A.draws), A.N, A.d, rng), A.day, A.n_days, A.N)
        sb = day_indicator(fast_draws(len(B.draws), B.N, B.d, rng), B.day, B.n_days, B.N)
        sims.append(cross_stats(A, sa, B, sb))
    days = real.pop("common_days")
    for s in sims:
        s.pop("common_days")
    return f"{ga}~{gb}", {"common_days": int(days), "tests": _mc_pvalues(real, sims)}


def weighted_draws(rows: int, w: np.ndarray, d: int, rng: np.random.Generator) -> np.ndarray:
    """Extragere ponderata fara intoarcere (Plackett-Luce), prin cheile Gumbel."""
    keys = np.log(w)[None, :] + rng.gumbel(size=(rows, len(w)))
    return np.argsort(-keys, axis=1)[:, :d] + 1


def _power_one(args):
    game, seed = args
    data = load_game(game)
    rows, d, N = len(data.draws), data.d, data.N
    K, t = 16, 4
    rng = np.random.default_rng(seed)
    base = sum(comb(K, h) * comb(N - K, d - h) for h in range(t, min(K, d) + 1)) / comb(N, d)

    def chi2_freq(dr):
        c = indicator(dr, N).sum(0)
        e = rows * d / N
        return float(((c - e) ** 2).sum() / e)

    null = np.array([chi2_freq(fast_draws(rows, N, d, rng)) for _ in range(1000)])
    crit = float(np.quantile(null, 0.95))
    out = []
    for eps in EPS_GRID:
        w = np.ones(N)
        w[:K] = 1.0 + eps
        big = weighted_draws(200_000, w, d, rng)
        rate = float(((big <= K).sum(1) >= t).mean())
        power = float(np.mean([chi2_freq(weighted_draws(rows, w, d, rng)) > crit for _ in range(400)]))
        # Testul cel mai puternic posibil: pool-ul favorizat cunoscut dinainte,
        # binomial pe evenimentul 4+ la nivel 5%.
        k_crit = int(binom.isf(0.05, rows, base)) + 1
        power_known = float(binom.sf(k_crit - 1, rows, rate))
        out.append(
            {
                "eps": eps,
                "rate_4plus": rate,
                "base_4plus": base,
                "relative_gain": rate / base - 1.0,
                "power_freq_chi2": power,
                "power_known_pool_binomial": power_known,
            }
        )
    return game, {"rows": rows, "K": K, "target": t, "grid": out}


# ----------------------------------------------------------------- part B


def source_state(src, tgt, kind: str, param) -> np.ndarray:
    """Starea sursei din zilele cu data strict inaintea zilei tinta, pe numerele tintei."""
    C = np.vstack([src.C, np.zeros((1, src.N))])
    if kind == "last":
        St = ps.shift(C)
    elif kind == "ewma":
        St = ps.ewma_state(C, param)
    else:
        St = ps.window(ps.cumsum0(C), param)
    j = np.searchsorted(src.day_dates, tgt.day_dates, side="left")
    Ss = St[j]
    m = min(tgt.N, src.N)
    S = np.empty((tgt.n_days, tgt.N))
    S[:, :m] = Ss[:, :m]
    if tgt.N > m:
        S[:, m:] = Ss[:, :m].mean(axis=1, keepdims=True)
    return S


CROSS_KINDS = (
    ("last", None, "last"),
    ("ewma", 0.02, "ewma0.02"),
    ("ewma", 0.1, "ewma0.1"),
    ("ewma", 0.3, "ewma0.3"),
    ("win", 50, "win50"),
    ("win", 200, "win200"),
)


def era_scores(data, W: int, alpha: float, step: int = 10) -> np.ndarray:
    CS = ps.cumsum0(data.C)
    n, N, d = data.n_days, data.N, data.d
    scale = (N - 1) / (N - d) if d < N else 1.0
    start = 0
    S = np.zeros((n, N))
    for day in range(n):
        if day >= 5 * W and day % step == 0:
            recent = CS[day] - CS[day - W]
            before = CS[day - W] - CS[day - 5 * W]
            stat = contingency_chi2(np.vstack([recent, before])) * scale
            if chi2.sf(stat, N - 1) < alpha:
                start = max(start, day - W)
        S[day] = CS[day] - CS[start]
    return S


def new_scores(game: str, data, sources: dict):
    for src_game in SOURCES.get(game, ()):
        src = sources.get(src_game)
        if src is None:
            continue
        for kind, param, tag in CROSS_KINDS:
            S = source_state(src, data, kind, param)
            yield f"x{SHORT[src_game]}_{tag}", "cross", S
            yield f"anti_x{SHORT[src_game]}_{tag}", "cross", -S
    for W in (50, 100, 200):
        for alpha in (0.01, 0.001):
            S = era_scores(data, W, alpha)
            yield f"era_w{W}_a{alpha}", "era", S
            yield f"anti_era_w{W}_a{alpha}", "era", -S


def ftl_choose(Hday: np.ndarray, L: int | None = None, delta: float | None = None) -> np.ndarray:
    """Indexul scorerului ales in fiecare zi, numai din hiturile zilelor anterioare."""
    n_s, n_days = Hday.shape
    if L is not None:
        CS = np.concatenate([np.zeros((n_s, 1)), np.cumsum(Hday, axis=1)], axis=1)
        d = np.arange(n_days)
        V = CS[:, d] - CS[:, np.maximum(0, d - L)]
    else:
        V = np.zeros((n_s, n_days))
        acc = np.zeros(n_s)
        for d in range(n_days):
            V[:, d] = acc
            acc = delta * acc + Hday[:, d]
    return np.argmax(V, axis=0)


FTL_RULES = [("L50", 50, None), ("L100", 100, None), ("L200", 200, None), ("L400", 400, None),
             ("d0.99", None, 0.99), ("d0.995", None, 0.995)]


def bank_hits(data, Ks):
    """Hiturile fiecarui scorer din 2026-10-05, pe rand si pe zi, pentru fiecare K."""
    names, fams = [], []
    H = {K: [] for K in Ks}
    T = {K: [] for K in Ks}
    for name, fam, S in ps.bank(data):
        if not np.all(np.isfinite(S)):
            continue
        ev = ps.evaluate(S, data, Ks)
        names.append(name)
        fams.append(fam)
        for K, (h, tie) in ev.items():
            H[K].append(h)
            T[K].append(tie)
    fams = np.array(fams)
    sets = {"freq": np.flatnonzero(np.isin(fams, ("frequency", "ewma"))), "bank": np.arange(len(names))}
    out = {}
    for K in Ks:
        Hk = np.stack(H[K])
        Hday = np.zeros((data.n_days, len(names)))
        np.add.at(Hday, data.day, Hk.T.astype(float))
        out[K] = (Hk, np.stack(T[K]), Hday.T)
    return sets, out


def ftl_candidates(data, Ks):
    """(nume, familie, K, hits, tie) pentru meta-selectiile peste scorerii din 2026-10-05."""
    sets, by_k = bank_hits(data, Ks)
    rows = np.arange(len(data.draws))
    for K in Ks:
        Hk, Tk, Hday = by_k[K]
        for set_name, idx in sets.items():
            for tag, L, delta in FTL_RULES:
                pick = idx[ftl_choose(Hday[idx], L, delta)][data.day]
                yield f"ftl_{set_name}_{tag}", "ftl", K, Hk[pick, rows], Tk[pick, rows]


def candidates(game: str, data, sources: dict, Ks):
    for name, fam, S in new_scores(game, data, sources):
        if not np.all(np.isfinite(S)):
            continue
        for K, (h, tie) in ps.evaluate(S, data, Ks).items():
            yield name, fam, K, h, tie
    yield from ftl_candidates(data, Ks)


def screen(game: str, data, sources: dict, keep_hits: bool = False):
    Ks = GAMES[game]["Ks"]
    m = ps.masks(data)
    out, store = [], {}
    for name, fam, K, hits, tie in candidates(game, data, sources, Ks):
        t = target_for(game, K)
        dev = ps.stats(hits, m["dev"], data.N, data.d, K, t)
        h1 = ps.stats(hits, m["dev1"], data.N, data.d, K, t)
        h2 = ps.stats(hits, m["dev2"], data.N, data.d, K, t)
        tie_rate = float(tie[m["dev"]].mean())
        out.append(
            {
                "name": name,
                "family": fam,
                "K": K,
                "target": t,
                "tie_rate": tie_rate,
                "gated": tie_rate > ps.TIE_GATE,
                "stable": h1["excess_hits"] > 0 and h2["excess_hits"] > 0,
                "dev_z_hits": dev["z_hits"],
                "dev_z_event": dev["z_event"],
                "dev_rate": dev["rate"],
                "dev_p0": dev["p0"],
                "dev_n": dev["n"],
            }
        )
        if keep_hits:
            store[(name, K)] = hits
    return pd.DataFrame(out), store, m


def synthetic_set(game: str, rng: np.random.Generator) -> tuple[object, dict]:
    base = load_game(game)
    syn = ps.synthetic(base, rng)
    sources = {g: ps.synthetic(load_game(g), rng) for g in SOURCES.get(game, ())}
    return syn, sources


def _null_one(args):
    game, seed = args
    syn, sources = synthetic_set(game, np.random.default_rng(seed))
    table, _, _ = screen(game, syn, sources)
    ok = table[~table["gated"]]
    st = ok[ok["stable"]]
    k16 = st[st["K"] == 16]
    return {
        "max_z_hits": float(st["dev_z_hits"].max()) if len(st) else float("-inf"),
        "max_z_event": float(st["dev_z_event"].max()) if len(st) else float("-inf"),
        "max_z_event_k16": float(k16["dev_z_event"].max()) if len(k16) else float("-inf"),
        "mean_z_hits": float(ok["dev_z_hits"].mean()),
        "sd_z_hits": float(ok["dev_z_hits"].std()),
    }


def select(table: pd.DataFrame) -> list[dict]:
    ok = table[(~table["gated"]) & (table["stable"])]
    picked, keys, names = [], set(), set()

    def take(r, by):
        picked.append({"name": r["name"], "family": r["family"], "K": int(r["K"]), "target": int(r["target"]),
                       "by": by, "dev_z_hits": float(r["dev_z_hits"]), "dev_z_event": float(r["dev_z_event"]),
                       "dev_rate": float(r["dev_rate"]), "dev_p0": float(r["dev_p0"])})
        keys.add((r["name"], int(r["K"])))
        names.add(r["name"])

    for col, n in (("dev_z_hits", 3), ("dev_z_event", 2)):
        taken = 0
        for _, r in ok.sort_values([col, "name", "K"], ascending=[False, True, True]).iterrows():
            if r["name"] in names:
                continue
            take(r, col)
            taken += 1
            if taken == n:
                break
    k16 = ok[ok["K"] == 16].sort_values(["dev_z_event", "name"], ascending=[False, True])
    for _, r in k16.iterrows():
        if (r["name"], 16) not in keys:
            take(r, "k16_event")
        break
    return picked


def leak_check(game: str) -> int:
    """Schimba o zi a tintei si o zi a fiecarei surse: scorurile si alegerile FTL
    pana la acea zi inclusiv (pentru sursa: pana la data ei inclusiv) raman identice."""
    data = load_game(game)
    sources = {g: load_game(g) for g in SOURCES.get(game, ())}
    rng = np.random.default_rng(7)

    def altered(base, d0):
        draws = base.draws.copy()
        for r in np.flatnonzero(base.day == d0):
            draws[r] = rng.permutation(base.N)[: base.d] + 1
        return ps.Data(draws, base.day, base.day_dates, base.N, base.ordered)

    d0 = data.n_days // 2
    alt = altered(data, d0)
    checked = 0
    for (n1, _f, S1), (n2, _g, S2) in zip(new_scores(game, data, sources), new_scores(game, alt, sources)):
        assert n1 == n2
        if not np.array_equal(S1[: d0 + 1], S2[: d0 + 1]):
            raise AssertionError(f"leak in {n1} (target)")
        checked += 1
    # FTL: alegerea din ziua d0 se face fara extragerile zilei d0.
    K = GAMES[game]["Ks"][-1]
    sets, b1 = bank_hits(data, (K,))
    _sets, b2 = bank_hits(alt, (K,))
    for set_name, idx in sets.items():
        for tag, L, delta in FTL_RULES:
            c1 = ftl_choose(b1[K][2][idx], L, delta)
            c2 = ftl_choose(b2[K][2][idx], L, delta)
            if not np.array_equal(c1[: d0 + 1], c2[: d0 + 1]):
                raise AssertionError(f"leak in ftl_{set_name}_{tag} (choice)")
            checked += 1
    for g, src in sources.items():
        j0 = src.n_days // 2
        moved = dict(sources)
        moved[g] = altered(src, j0)
        cutoff = np.searchsorted(data.day_dates, src.day_dates[j0], side="right")
        for (n1, _f, S1), (n2, _g2, S2) in zip(new_scores(game, data, sources), new_scores(game, data, moved)):
            if not np.array_equal(S1[:cutoff], S2[:cutoff]):
                raise AssertionError(f"leak in {n1} (source {g})")
            checked += 1
    return checked


# ----------------------------------------------------------------- part C


def hindsight_rate(draws: np.ndarray, N: int, K: int = 16, t: int = 4) -> tuple[float, list[int]]:
    c = indicator(draws, N).sum(0)
    order = np.lexsort((-np.arange(N), -c))
    pool = order[:K]
    inside = np.isin(draws - 1, pool).sum(1)
    return float((inside >= t).mean()), sorted(int(x) + 1 for x in pool)


def _hindsight_one(args):
    label, seed = args
    data = dict(all_series())[label]
    rows, d = data.draws.shape
    real, pool = hindsight_rate(data.draws, data.N)
    rng = np.random.default_rng(seed)
    sims = np.array([hindsight_rate(fast_draws(rows, data.N, d, rng), data.N)[0] for _ in range(M_BATTERY)])
    K, t, N = 16, 4, data.N
    base = sum(comb(K, h) * comb(N - K, d - h) for h in range(t, min(K, d) + 1)) / comb(N, d)
    return label, {
        "rows": rows,
        "pool": pool,
        "rate_4plus": real,
        "base_4plus": base,
        "null_mean": float(sims.mean()),
        "null_q95": float(np.quantile(sims, 0.95)),
        "p": float((1 + np.sum(sims >= real)) / (len(sims) + 1)),
    }


# ----------------------------------------------------------------- main


def bh(pvals: list[float]) -> list[float]:
    m = len(pvals)
    order = np.argsort(pvals)
    adj = np.empty(m)
    prev = 1.0
    for rank in range(m - 1, -1, -1):
        idx = order[rank]
        prev = min(prev, pvals[idx] * m / (rank + 1))
        adj[idx] = prev
    return [float(x) for x in adj]


def main() -> None:
    t0 = time.time()
    report: dict = {"protocol": __doc__}
    labels = [lab for lab, _ in all_series()]

    # ---- A
    jobs_a = [(lab, SEED + 17 * i) for i, lab in enumerate(labels)]
    pairs = [("ro_649", "ro_540"), ("ro_649", "ro_joker1"), ("ro_540", "ro_joker1"),
             ("ro_649", "ro_joker2"), ("ro_540", "ro_joker2")]
    jobs_x = [(a, b, SEED + 500 + i) for i, (a, b) in enumerate(pairs)]
    jobs_p = [(g, SEED + 900 + i) for i, g in enumerate(("ro_649", "ro_540", "ro_joker1"))]
    jobs_c = [(lab, SEED + 1300 + i) for i, lab in enumerate(labels) if lab != "ro_joker2"]
    with ProcessPoolExecutor(max_workers=WORKERS) as ex:
        battery = dict(ex.map(_battery_one, jobs_a))
        cross = dict(ex.map(_cross_one, jobs_x))
        power = dict(ex.map(_power_one, jobs_p))
        hindsight = dict(ex.map(_hindsight_one, jobs_c))
    flat = [(lab, k, v) for lab, r in battery.items() for k, v in r["tests"].items()]
    flat += [(lab, k, v) for lab, r in cross.items() for k, v in r["tests"].items()]
    ph = ps.holm([v["p"] for _l, _k, v in flat])
    pb = bh([v["p"] for _l, _k, v in flat])
    for (lab, k, v), a, b in zip(flat, ph, pb):
        v["holm"] = a
        v["bh"] = b
    report["battery"] = battery
    report["cross_battery"] = cross
    report["battery_summary"] = {
        "tests": len(flat),
        "min_p": float(min(v["p"] for _l, _k, v in flat)),
        "min_holm": float(min(ph)),
        "min_bh": float(min(pb)),
        "below_005_raw": int(sum(v["p"] < 0.05 for _l, _k, v in flat)),
        "expected_below_005": 0.05 * len(flat),
        "smallest": sorted(
            ({"series": lab, "test": k, **v} for lab, k, v in flat), key=lambda r: r["p"]
        )[:12],
    }
    report["power"] = power
    report["hindsight"] = hindsight
    print(f"[A] {len(flat)} tests, min p={report['battery_summary']['min_p']:.4f}, "
          f"min holm={report['battery_summary']['min_holm']:.3f}, raw<0.05: "
          f"{report['battery_summary']['below_005_raw']} (expected {0.05 * len(flat):.1f}) "
          f"({time.time() - t0:.0f}s)", flush=True)

    # ---- B
    games = list(GAMES)
    for g in games:
        print(f"[leak] {g}: {leak_check(g)} checks, no future data", flush=True)
    real = {}
    all_sources = {g: load_game(g) for g in GAMES}
    for g in games:
        data = all_sources[g]
        srcs = {s: all_sources[s] for s in SOURCES[g]}
        table, hits, m = screen(g, data, srcs, keep_hits=True)
        real[g] = (data, table, hits, m)
        ok = table[~table["gated"]]
        print(f"[{g}] candidates={table['name'].nunique()} tests={len(table)} gated={int(table['gated'].sum())} "
              f"max z_hits={ok['dev_z_hits'].max():.2f} ({time.time() - t0:.0f}s)", flush=True)
    jobs = [(g, SEED + 10_000 + 1000 * i + j) for i, g in enumerate(games) for j in range(N_NULL)]
    null: dict[str, list] = {g: [] for g in games}
    with ProcessPoolExecutor(max_workers=WORKERS) as ex:
        for (g, _s), res in zip(jobs, ex.map(_null_one, jobs, chunksize=2)):
            null[g].append(res)
    print(f"[null] {len(jobs)} synthetic screens ({time.time() - t0:.0f}s)", flush=True)

    report["games"] = {}
    ro_tests = []
    for g in games:
        data, table, hits, m = real[g]
        ok = table[~table["gated"]]
        st = ok[ok["stable"]]
        k16 = st[st["K"] == 16]
        nh = np.array([r["max_z_hits"] for r in null[g]])
        ne = np.array([r["max_z_event"] for r in null[g]])
        n16 = np.array([r["max_z_event_k16"] for r in null[g]])
        best_h = float(st["dev_z_hits"].max())
        best_e = float(st["dev_z_event"].max())
        best_16 = float(k16["dev_z_event"].max()) if len(k16) else float("nan")
        picked = select(table)
        for c in picked:
            s = ps.stats(hits[(c["name"], c["K"])], m["conf"], data.N, data.d, c["K"], c["target"])
            c["confirm"] = s
            c["p_hits"] = float(norm.sf(s["z_hits"]))
            c["p_event"] = float(binomtest(s["events"], s["n"], s["p0"], alternative="greater").pvalue)
            ro_tests.append((g, c))
        report["games"][g] = {
            "rows": int(len(data.draws)),
            "days": int(data.n_days),
            "candidates": int(table["name"].nunique()),
            "tests": int(len(table)),
            "gated": int(table["gated"].sum()),
            "stable": int(len(st)),
            "families": table.groupby("family")["name"].nunique().to_dict(),
            "dev_best_z_hits": best_h,
            "dev_best_z_event": best_e,
            "dev_best_z_event_k16": best_16,
            "null_q95_z_hits": float(np.quantile(nh, 0.95)),
            "null_q95_z_event": float(np.quantile(ne, 0.95)),
            "null_q95_z_event_k16": float(np.quantile(n16[np.isfinite(n16)], 0.95)) if np.isfinite(n16).any() else None,
            "p_search_hits": float((1 + np.sum(nh >= best_h)) / (len(nh) + 1)),
            "p_search_event": float((1 + np.sum(ne >= best_e)) / (len(ne) + 1)),
            "p_search_event_k16": float((1 + np.sum(n16 >= best_16)) / (len(n16) + 1)) if len(k16) else None,
            "null_mean_z": float(np.mean([r["mean_z_hits"] for r in null[g]])),
            "null_sd_z": float(np.mean([r["sd_z_hits"] for r in null[g]])),
            "real_mean_z": float(ok["dev_z_hits"].mean()),
            "real_sd_z": float(ok["dev_z_hits"].std()),
            "by_family": ok.groupby("family")["dev_z_hits"].agg(["count", "mean", "max"]).to_dict(orient="index"),
            "top_dev": ok.sort_values("dev_z_hits", ascending=False).head(12).to_dict(orient="records"),
            "selected": picked,
        }
        r = report["games"][g]
        print(f"[{g}] dev best z_hits={best_h:.2f} (null q95 {r['null_q95_z_hits']:.2f}, p={r['p_search_hits']:.3f}); "
              f"best z_event={best_e:.2f} (q95 {r['null_q95_z_event']:.2f}, p={r['p_search_event']:.3f})", flush=True)

    adj_h = ps.holm([c["p_hits"] for _g, c in ro_tests])
    adj_e = ps.holm([c["p_event"] for _g, c in ro_tests])
    for (_g, c), a, b in zip(ro_tests, adj_h, adj_e):
        c["holm_hits"] = a
        c["holm_event"] = b

    externals = {}
    for g in games:
        for c in report["games"][g]["selected"]:
            if c["family"] == "cross" or g not in EXTERNAL:
                c["external"] = None
                continue
            rows_ext = []
            for label, path, cols, N in EXTERNAL[g]:
                if label not in externals:
                    externals[label] = ps.load(path, cols, N)
                ext = externals[label]
                h = None
                if c["family"] == "era":
                    for name, _f, S in new_scores(g, ext, {}):
                        if name == c["name"]:
                            h = ps.evaluate(S, ext, (c["K"],))[c["K"]][0]
                            break
                else:
                    for name, _f, K, hh, _t in ftl_candidates(ext, (c["K"],)):
                        if name == c["name"]:
                            h = hh
                            break
                s = ps.stats(h, ext.day >= ps.WARMUP_DAYS, ext.N, ext.d, c["K"], c["target"])
                rows_ext.append({"lottery": label, **s})
            tot_h = sum(r["hits"] for r in rows_ext)
            mu = sum(r["n"] * ps.hyp_mean_var(externals[r["lottery"]].N, externals[r["lottery"]].d, c["K"])[0]
                     for r in rows_ext)
            var = sum(r["n"] * ps.hyp_mean_var(externals[r["lottery"]].N, externals[r["lottery"]].d, c["K"])[1]
                      for r in rows_ext)
            z = (tot_h - mu) / sqrt(var)
            c["external"] = {"rows": rows_ext, "hits": tot_h, "expected": mu, "z_hits": z, "p_hits": float(norm.sf(z))}

    for g in games:
        for c in report["games"][g]["selected"]:
            c["promote"] = bool(
                c["holm_hits"] < 0.05
                and c["confirm"]["z_hits"] > 0
                and (c["external"] is None or c["external"]["p_hits"] < 0.05)
            )
            ext = f" ext p={c['external']['p_hits']:.3f}" if c["external"] else ""
            print(f"[{g}] {c['name']} K={c['K']} conf {c['confirm']['rate'] * 100:.2f}% vs "
                  f"{c['confirm']['p0'] * 100:.2f}% z_hits={c['confirm']['z_hits']:.2f} "
                  f"holm_hits={c['holm_hits']:.3f} holm_event={c['holm_event']:.3f}{ext} "
                  f"promote={c['promote']}", flush=True)

    report["promoted"] = [
        {"game": g, "name": c["name"], "K": c["K"]}
        for g in games
        for c in report["games"][g]["selected"]
        if c["promote"]
    ]
    report["runtime_sec"] = time.time() - t0
    dest = HERE / "hit_screen_2026-10-09.json"
    dest.write_text(json.dumps(report, indent=1, default=float), encoding="utf-8")
    print(f"done {time.time() - t0:.0f}s promoted={report['promoted']}", flush=True)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--leak":
        for g in GAMES:
            print(g, leak_check(g))
    else:
        main()
