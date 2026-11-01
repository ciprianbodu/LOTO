"""Ecran 2 de metode pentru hituri 4+/5+, 2026-11-01: ansambluri dinamice si semnale intre jocuri. Protocol fixat inainte de rulare.

Continuare dupa hit5plus_screen_2026-11-01.py (42 de scoreri statici, nimic
promovat). Aici se testeaza ce a ramas: META-scoreri peste o banca de scoreri
de baza si semnale INTRE jocurile romanesti, masurati la pragurile 4+ si 5+.

Date, celule, nul, selectie, confirmare: identic cu hit5plus_screen_2026-11-01.py
(Romania 6/49, 5/40, Joker Urna 1; pool (11,16) x tinta (4+,5+); 200 zile
warmup; 70% dezvoltare / 30% confirmare; nul pe N_NULL istorii sintetice;
lider = decizia din folds.csv la tinta 4; McNemar + binomial + Holm +
replicare externa; promovare doar cu Holm < 0.05 pe ambele si p extern < 0.05).

Candidati noi (banca de baza: freq50/freq200/freqall, ewma 0.05/0.2, markov
lag1, co-ocurrenta, pair-lift dezintegrat h=30, triple-lift L=500 — toti
S[d] vad numai zilele < d):
  E1 FTL la 5+ — „urmareste liderul" la evenimentul 5+ (B2 din 09.10 l-a
     rulat la 4+): alegem zilnic scorerul de baza cu cele mai multe evenimente
     5+ in ultimele L zile (discount exponential), K in (11,16), L in
     (100,200), discount in (1.0, 0.99); si varianta anti-FTL (cel mai slab).
  E3 voturi — media rangurilor peste toata banca; numarul de scoreri de baza
     care pun n in top-K (K = 11, 16).
  E4 stacking — regresie logistica online (ps.logistic_online) pe rangurile
     primelor 6 scoreri de baza, lambda 1 / 100.
  E5 Kalman — nivel local per numar (Q in 1e-4/1e-3, R = 0.1), si anti.
  E2 cross-game — pentru fiecare joc romanesc, suma frecventelor pe ultimele
     W zile (W = 50, 200) ale CELORLALTE doua jocuri romanesti, aliniate pe
     indexul de zi, n -> n; si anti. B1 din 09.10 a testat cross-game la 4+;
     aici e la 4+ si 5+. In nul, cele 3 jocuri sintetice se genereaza din
     acelasi stream de seed, deci si E2 are nulul lui. E2 nu are replicare
     externa (nu exista istorice paralele pentru straine).

Check-uri: leak si echivarianța se verifica pe toate familiile; pentru E2
se permuta aceeasi permutare pe TOATE cele 3 jocuri (cross copiaza n -> n din
alt joc, deci echivarianța tine doar cu permutare comuna) si leak-ul se
verifica modificand aceeasi zi in toate jocurile.
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
from math import sqrt
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import binom, binomtest, norm

HERE = Path(__file__).resolve().parent
os.environ.setdefault("HIT5_NULL", "100")
os.environ.setdefault("HIT5_WORKERS", "16")
os.environ.setdefault("HIT5_OUT", str(HERE / "hit5plus_screen2_2026-11-01.json"))

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
N_NULL = h5.N_NULL
WORKERS = h5.WORKERS
OUT = Path(os.environ["HIT5_OUT"])
GAMES = h5.GAMES
EXTERNAL = h5.EXTERNAL

# Stare globala pentru familia cross-game (setata per joc inainte de screen).
_GAME: str | None = None
CROSS: dict[str, dict[str, np.ndarray]] = {}


# ------------------------------------------------------------------ helpers


def _base_bank(data) -> dict[str, np.ndarray]:
    N, Ib, C = data.N, data.Ib, data.C
    CS = ps.cumsum0(C)
    return {
        "freq50": ps.window(CS, 50),
        "freq200": ps.window(CS, 200),
        "freqall": CS[:-1],
        "ewma0.05": ps.ewma_state(C, 0.05),
        "ewma0.2": ps.ewma_state(C, 0.2),
        "markov1": ps.markov(Ib, 1),
        "cooc": ps.cooccurrence(Ib, None, 20.0),
        "pairlift30": h5._decayed_pair_lift_scores(Ib, N, 30.0),
        "triple500": h5._subset_scores(Ib, N, data.d, 3, 500, True),
    }


def _rank_avg(S: np.ndarray) -> np.ndarray:
    """Rang per linie in [0,1], cu medie la egalitate — echivariant la
    permutarea coloanelor (spre deosebire de argsort stable, care rupe
    egalitatile dupa index). Rotunjirea la 10 zecimale sterge zgomotul float
    (~1e-16) care altfel ar rupe egalitatile exacte si ar schimba rangurile
    medii cu O(1/N) sub permutare."""
    from scipy.stats import rankdata

    return rankdata(np.round(S, 10), axis=1, method="average") / max(1, S.shape[1] - 1)


def _hits_equiv(S: np.ndarray, data, K: int) -> np.ndarray:
    """Hit-uri per rand cu top-K pe prag de rang (echivariant la etichete,
    spre deosebire de ps.evaluate care foloseste tie-break pe index)."""
    N = S.shape[1]
    top = (_rank_avg(S) >= (N - K) / (N - 1))[data.day]  # per rand
    present = np.zeros(top.shape, dtype=bool)
    r = np.arange(len(data.day))[:, None]
    present[r, data.draws - 1] = True
    return (present & top).sum(axis=1)


def _logistic_online2(features: list[np.ndarray], Ib: np.ndarray, lam: float, warm: int) -> np.ndarray:
    """ps.logistic_online, dar cu _rank_avg (echivariant) pe features."""
    n_days, N = Ib.shape
    F = np.stack([_rank_avg(f) - 0.5 for f in features], axis=2)
    F = np.concatenate([np.ones((n_days, N, 1)), F], axis=2)
    S = np.zeros((n_days, N))
    lo = 50
    for fit_at in range(max(warm, lo + 50), n_days, ps.REFIT_EVERY):
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
        end = min(fit_at + ps.REFIT_EVERY, n_days)
        S[fit_at:end] = F[fit_at:end] @ w
    return S


def _kalman_scores(C: np.ndarray, N: int, Q: float, R: float) -> np.ndarray:
    n_days = C.shape[0]
    m = np.zeros(N)
    P = np.ones(N)
    S = np.zeros((n_days, N))
    for d in range(n_days):
        S[d] = m  # stare dupa ce am observat zilele < d
        Pp = P + Q
        K = Pp / (Pp + R)
        m = m + K * (C[d] - m)
        P = (1.0 - K) * Pp
    return S


def _ftl_scores(data, base: dict[str, np.ndarray], K: int, L: int, discount: float, anti: bool) -> np.ndarray:
    """Alege zilnic scorerul de baza cu cele mai multe evenimente 5+ in
    ultimele L zile (discount exponential); S[d] = scorul alesului la d."""
    N = data.N
    n_days = data.n_days
    first = np.flatnonzero(np.r_[True, data.day[1:] != data.day[:-1]])
    dh = {}
    for n, S in base.items():
        h = _hits_equiv(S, data, K)
        dh[n] = h[first]
    names = list(base)
    S_out = np.zeros((n_days, N))
    for d in range(n_days):
        lo = max(0, d - L)
        w = discount ** np.arange(d - 1 - lo, -1, -1) if d > lo else np.zeros(0)
        best, bestv = names[0], -np.inf
        for n in names:
            ev = (dh[n][lo:d] >= 5).astype(float)
            v = float((ev * w).sum())
            if anti:
                v = -v
            if v > bestv:
                bestv, best = v, n
        S_out[d] = base[best][d]
    return S_out


def _build_cross(games_data: dict) -> dict[str, dict[str, np.ndarray]]:
    """Pentru fiecare joc, suma ferestrelor de frecventa ale celorlalte jocuri,
    aliniate pe indexul de zi."""
    cross: dict[str, dict[str, np.ndarray]] = {}
    for g, data in games_data.items():
        acc: dict[str, np.ndarray] = {}
        for W in (50, 200):
            S = np.zeros((data.n_days, data.N))
            for g2, d2 in games_data.items():
                if g2 == g:
                    continue
                Wm = ps.window(ps.cumsum0(d2.C), W)
                n = min(data.n_days, Wm.shape[0])
                m = min(data.N, d2.N)  # n -> n are sens doar pe 1..min(N)
                S[:n, :m] += Wm[:n, :m]
            acc[f"cross_W{W}"] = S
        cross[g] = acc
    return cross


# ------------------------------------------------------------------- the bank


def bank2(data):
    """Generator (nume, familie, scor zile x numere); S[d] vede numai zilele < d."""
    N, Ib, C = data.N, data.Ib, data.C
    base = _base_bank(data)
    names = list(base)
    ranks = {n: _rank_avg(S) for n, S in base.items()}

    def both(name, fam, S):
        yield name, fam, S
        yield f"anti_{name}", fam, -S

    # E3 voturi (top-K pe prag de rang — echivariant; la egalitate intra toate)
    yield "vote_meanrank", "E3_vote", np.mean([ranks[n] for n in names], axis=0)
    for K in (11, 16):
        thr = (N - K) / (N - 1)
        S_vote = np.zeros((data.n_days, N))
        for n in names:
            S_vote += ranks[n] >= thr
        yield f"vote_top{K}", "E3_vote", S_vote

    # E1 FTL la 5+ (si anti-FTL)
    for K in (11, 16):
        for L in (100, 200):
            for disc in (1.0, 0.99):
                for anti in (False, True):
                    tag = f"ftl5_K{K}_L{L}_d{disc}" + ("_anti" if anti else "")
                    yield tag, "E1_ftl5", _ftl_scores(data, base, K, L, disc, anti)

    # E4 stacking logistica online pe ranguri (echivariante)
    feats = [ranks[n] for n in names[:6]]
    for lam in (1.0, 100.0):
        yield f"stack_l{int(lam)}", "E4_stack", _logistic_online2(feats, Ib, lam, ps.WARMUP_DAYS)

    # E5 Kalman nivel local
    for Q in (1e-4, 1e-3):
        yield from both(f"kalman_Q{Q:g}", "E5_kalman", _kalman_scores(C, N, Q, 0.1))

    # E2 cross-game (doar cand stim jocul curent)
    if _GAME is not None and _GAME in CROSS:
        for name, S in CROSS[_GAME].items():
            yield from both(name, "E2_cross", S)


h5.bank = bank2  # h5.screen / h5.leak_check folosesc acum banca noua


# ---------------------------------------------------------------- checks


def leak_check2() -> int:
    """Fara leak: schimbam extragerile unei zile in TOATE jocurile; scorurile
    pana la acea zi inclusiv (inclusiv cross-game) raman identice."""
    global _GAME, CROSS
    games_data = {g: h5.load_game(g) for g in GAMES}
    d0 = games_data["ro_649"].n_days // 2
    rng = np.random.default_rng(7)
    alt_data = {}
    for g, data in games_data.items():
        draws = data.draws.copy()
        for r in np.flatnonzero(data.day == d0):
            draws[r] = rng.permutation(data.N)[: data.d] + 1
        alt_data[g] = ps.Data(draws, data.day, data.day_dates, data.N, data.ordered)
    cross_orig = _build_cross(games_data)
    cross_alt = _build_cross(alt_data)
    checked = 0
    for g in GAMES:
        _GAME = g
        CROSS = cross_orig
        gen1 = list(bank2(games_data[g]))
        CROSS = cross_alt
        gen2 = list(bank2(alt_data[g]))
        for (n1, _f1, S1), (n2, _f2, S2) in zip(gen1, gen2):
            assert n1 == n2
            if not np.allclose(S1[: d0 + 1], S2[: d0 + 1], rtol=0, atol=1e-12):
                raise AssertionError(f"leak in {n1} (game {g})")
            checked += 1
    _GAME = None
    CROSS = {}
    return checked


def equivariance_check2() -> int:
    """Echivarianța: aceeasi permutare pe 1..min(N) aplicata TUTUROR jocurilor
    (E2 copiaza n -> n din alt joc, deci tine doar cu permutare comuna)."""
    global _GAME, CROSS
    games_data = {g: h5.load_game(g) for g in GAMES}
    nmin = min(d.N for d in games_data.values())
    rng = np.random.default_rng(11)
    perm = rng.permutation(nmin)
    alt_data = {}
    perms = {}
    for g, data in games_data.items():
        p = np.arange(data.N)
        p[:nmin] = perm  # numerele 1..nmin devin perm+1, restul raman
        perms[g] = p
        alt_data[g] = ps.Data(p[data.draws - 1] + 1, data.day, data.day_dates, data.N, data.ordered)
    cross_orig = _build_cross(games_data)
    cross_alt = _build_cross(alt_data)
    checked = 0
    for g in GAMES:
        _GAME = g
        CROSS = cross_orig
        gen1 = list(bank2(games_data[g]))
        CROSS = cross_alt
        gen2 = list(bank2(alt_data[g]))
        for (n1, f1, S1), (n2, _f2, S2) in zip(gen1, gen2):
            assert n1 == n2
            # E4_stack rezolva un sistem liniar (np.linalg.solve cu pivotare) —
            # sub permutare pivotarea se schimba, deci zgomot de rotunjire ~1e-5.
            # Restul familiilor trebuie sa fie echivariante strict (1e-9).
            rtol, atol = (1e-6, 1e-4) if f1 == "E4_stack" else (1e-9, 1e-9)
            if not np.allclose(S1, S2[:, perms[g]], rtol=rtol, atol=atol):
                raise AssertionError(f"not equivariant: {n1} (game {g})")
            checked += 1
    _GAME = None
    CROSS = {}
    return checked


# ---------------------------------------------------------------- null


def _null_one2(args):
    game, seed = args
    logging.disable(logging.WARNING)
    global _GAME, CROSS
    rng = np.random.default_rng(seed)
    syn_all = {}
    for g in GAMES:
        syn_all[g] = ps.synthetic(h5.load_game(g), rng)
    CROSS = _build_cross(syn_all)
    _GAME = game
    table, _, _ = h5.screen(syn_all[game], game)
    _GAME = None
    CROSS = {}
    return h5.cell_max(table)


# ---------------------------------------------------------------- main


def main() -> None:
    global _GAME, CROSS
    logging.disable(logging.WARNING)
    t0 = time.time()
    fp = h5.data_fingerprint()
    report: dict = {"protocol": __doc__, "data_sha256": fp, "n_null": N_NULL, "games": {}}
    games = sys.argv[1:] or list(GAMES)

    print(f"[leak] {leak_check2()} scoreri fara date viitoare (inclusiv cross)", flush=True)
    print(f"[equivariance] {equivariance_check2()} scoreri verificati", flush=True)

    games_data = {g: h5.load_game(g) for g in GAMES}
    CROSS = _build_cross(games_data)

    frozen: list[dict] = []
    with ProcessPoolExecutor(WORKERS) as pool:
        for game in games:
            _GAME = game
            data = games_data[game]
            table, store, m = h5.screen(data, game, keep_hits=True)
            n_scorers = table["name"].nunique()
            print(f"[{game}] scoreri={n_scorers} teste={len(table)} gated={int(table['gated'].sum())} "
                  f"({time.time() - t0:.0f}s)", flush=True)
            obs = h5.cell_max(table)
            seeds = [(game, SEED + 1000 * (list(GAMES).index(game) + 1) + i) for i in range(N_NULL)]
            null = list(pool.map(_null_one2, seeds))
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
        _GAME = None

        # Replicare externa: doar familiile same-game (E2 nu are istorice paralele)
        ext_frozen = [f for f in frozen if f["family"] != "E2_cross"]
        h5.external_tests(ext_frozen, pool)
        for f in frozen:
            if f["family"] == "E2_cross":
                f["external"] = None
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
