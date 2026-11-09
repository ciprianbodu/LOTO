"""Ecranul valului 4 (2026-11-09): ~350 de metode × tipare de predicție 5+.

Întrebarea utilizatorului: pot sute de metode avansate de statistică și
probabilități să identifice tipare predictive pentru 5+ numere în pool,
începând cu pool de 6 numere?

PROTOCOL FIXAT ÎNAINTE DE REZULTATE (anti-HARKing):

Date. România: 6/49 (V=49, k=6 extrase), 5/40 (V=40, k=6), Joker Urna 1
(V=45, k=5). Cauzalitate pe ZI: scorul unei zile vede strict zilele
anterioare; extragerile din aceeași zi primesc același scor (convenția
ecranelor 2026-10-05/09/10 și 2026-11-01).

Ferestre. Warmup 200 de zile; confirmarea principală = ultimele 30% din
zilele post-warmup; secundar = tot post-warmup (100%).

Celule. Pooluri 6 (cerută explicit), 10 și poolul de producție
(16/19/15) × ținte 3+/4+/5+ × cele trei jocuri, plus celula „media
hiturilor" per pool. Pool-ul = top-K al traiectoriei predictive a zilei.

Evaluare. Fiecare extragere este numărată separat contra pool-ului zilei
(o zi cu două extrageri contribuie cu două probe independente la același
pool); rata de bază p0 este hipergeometrica exactă per extragere.

Putere statistică (calculată înainte de rulare, 6/49, n≈779):
- pool 6, ținta 5+: baseline 1.85e-5/extragere ⇒ 0.014 evenimente așteptate;
  semnificație Holm (m=356) ar cere ≥2 evenimente = 140× baseline.
  CONCLUZIE A PRIORI: 5+ la pool 6 NU este validabil empiric pe acest
  istoric; ecranul raportează acolo 3+/4+ și media de hituri.
- pool 6, ținta 4+: minim 7 evenimente pentru Holm.
- pool 16, ținta 5+: minim 22 evenimente pentru Holm (baseline 1.09%).

Teste. (a) binomial exact unilateral vs rata hipergeometrică exactă a
celulei; (b) z pe media hiturilor (media/dispersia exactă hipergeometrice).
Corecție Holm peste toată banca, separat per celulă; suplimentar raportăm
Holm global peste toate celulele.

Control anti-scurgere: două permutări aleatoare ale ORDINII zilelor
(semințe fixe 20261109, 20261110) distrug orice structură temporală;
toată banca rulează pe ele; orice p Holm-semnificativ acolo înseamnă
defect de harness, nu semnal.

Banca: `methods_wave4_bank_2026_11_09.py` (356 de predictori: Dirichlet/
Beta-Bernoulli/EWMA/Holt/Weibull/Markov/Hawkes/BOCPD/Fourier/faze/CUSUM/
KDE/entropie/logistic online/Hedge + mixturi liniare, geometrice, triple
și agregatori de rang). Grile fixate a priori în cod. Semnalele de bază se
calculează o singură dată per istorie (rezolvitor memoizat); mixturile le
refolosesc.

Ieșiri: `wave4_pattern_screen_2026_11_09_results.json` + sumar la stdout.
Promovarea în registry NU se face din acest ecran: ar cere confirmarea
porții oficiale Re-Bench (folds.csv) și decizia separată a utilizatorului.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import time

import numpy as np
import pandas as pd
from scipy.stats import binom, hypergeom, norm

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts.analysis.methods_wave4_bank_2026_11_09 import BASE_SIGNALS, build_bank

WARMUP_DAYS = 200
CONFIRM_TAIL = 0.30
SHUFFLE_SEEDS = (20261109, 20261110)
TARGETS = (3, 4, 5)
ALPHA = 0.05

GAMES = {
    "loto_6_49": {
        "csv": ROOT / "_ISTORIC" / "loto_6_49.csv",
        "cols": [f"n{i}" for i in range(1, 7)],
        "V": 49,
        "k": 6,
        "pools": (6, 10, 16),
    },
    "loto_5_40": {
        "csv": ROOT / "_ISTORIC" / "loto_5_40.csv",
        "cols": [f"n{i}" for i in range(1, 7)],
        "V": 40,
        "k": 6,
        "pools": (6, 10, 19),
    },
    "joker_urna1": {
        "csv": ROOT / "_ISTORIC" / "joker.csv",
        "cols": [f"n{i}" for i in range(1, 6)],
        "V": 45,
        "k": 5,
        "pools": (6, 10, 15),
    },
}


def load_day_matrix(
    csv: Path, cols: list[str], v: int
) -> tuple[np.ndarray, list[np.ndarray], list[int]]:
    """Istoricul cronologic → indicator pe ZILE (D×v), rândurile-extragere per
    zi (listă de matrice m×v) și numărul de extrageri per zi.

    Scorurile consumă un rând pe zi (reuniunea multimilor zilei); evaluarea
    numără fiecare extragere separat contra aceluiași pool.
    """
    df = pd.read_csv(csv)
    day = pd.to_datetime(df["date"], dayfirst=True)
    df = df.assign(_day=day).sort_values("_day", kind="stable").reset_index(drop=True)
    days: list[np.ndarray] = []
    day_draws: list[np.ndarray] = []
    draws_per_day: list[int] = []
    uniq = df["_day"].to_numpy()
    mat = df[cols].to_numpy(dtype=int)
    start = 0
    while start < len(df):
        end = start
        while end < len(df) and uniq[end] == uniq[start]:
            end += 1
        rows = np.zeros((end - start, v))
        for r, row in enumerate(mat[start:end]):
            rows[r, np.asarray(row) - 1] = 1.0
        days.append((rows.sum(axis=0) > 0).astype(float))
        day_draws.append(rows)
        draws_per_day.append(end - start)
        start = end
    return np.asarray(days), day_draws, draws_per_day


def make_resolver():
    """Rezolvitor memoizat al semnalelor de bază, valabil pentru O istorie."""
    cache: dict[str, np.ndarray] = {}

    def resolver(name: str, ind: np.ndarray) -> np.ndarray:
        traj = cache.get(name)
        if traj is None:
            traj = BASE_SIGNALS[name](ind)
            cache[name] = traj
        return traj

    return resolver


def hypergeom_p0(v: int, pool: int, k: int, target: int) -> float:
    return float(hypergeom.sf(target - 1, v, pool, k))


def count_hits(
    traj: np.ndarray,
    day_draws: list[np.ndarray],
    pools: tuple[int, ...],
    test_days: range,
) -> dict:
    """Pentru fiecare zi de test: pool top-K din scorul zilei; fiecare
    extragere a zilei este o probă separată contra aceluiași pool."""
    out = {K: {"n_draws": 0, "hits_sum": 0, "ge": {t: 0 for t in TARGETS}} for K in pools}
    for t in test_days:
        scores = traj[t]
        for K in pools:
            pool_idx = np.argpartition(-scores, K - 1)[:K]
            pool_mask = np.zeros(traj.shape[1], dtype=bool)
            pool_mask[pool_idx] = True
            cell = out[K]
            for row in day_draws[t]:
                h = int((pool_mask & (row > 0)).sum())
                cell["n_draws"] += 1
                cell["hits_sum"] += h
                for tgt in TARGETS:
                    if h >= tgt:
                        cell["ge"][tgt] += 1
    return out


def holm_flags(pvals: list[float], alpha: float = ALPHA) -> list[bool]:
    """Holm: True = respinge H0 după corecție, în ordinea listei date."""
    m = len(pvals)
    order = np.argsort(pvals)
    flags = [False] * m
    for rank, idx in enumerate(order):
        if pvals[idx] <= alpha / (m - rank):
            flags[idx] = True
        else:
            break
    return flags


def evaluate_cell(counts: dict, v: int, k: int, bank_names: list[str]) -> dict:
    """Teste exacte + Holm pentru o celulă (joc, fereastră); pools × ținte +
    media hiturilor per pool."""
    cells = {}
    for pool, per_method in counts.items():
        for tgt in TARGETS:
            p0 = hypergeom_p0(v, pool, k, tgt)
            rows = []
            pvals = []
            for name in bank_names:
                c = per_method[name]["ge"][tgt]
                n = per_method[name]["n_draws"]
                pv = float(binom.sf(c - 1, n, p0)) if c else 1.0
                rows.append({"method": name, "count": c, "n": n, "p": pv})
                pvals.append(pv)
            flags = holm_flags(pvals)
            for row, fl in zip(rows, flags):
                row["holm"] = fl
            rows.sort(key=lambda r: r["p"])
            cells[f"k{pool}_t{tgt}"] = {
                "p0": p0,
                "expected": p0 * (rows[0]["n"] if rows else 0),
                "top": rows[:10],
                "n_holm_significant": sum(flags),
            }
        # (b) media hiturilor per extragere vs hipergeometrica exactă
        mu = k * pool / v
        var = k * (pool / v) * (1.0 - pool / v) * (v - k) / (v - 1)
        rows = []
        pvals = []
        for name in bank_names:
            n = per_method[name]["n_draws"]
            mean_obs = per_method[name]["hits_sum"] / max(n, 1)
            z = (mean_obs - mu) / np.sqrt(var / max(n, 1))
            pv = float(norm.sf(z))
            rows.append({"method": name, "mean": round(mean_obs, 6), "n": n, "p": pv})
            pvals.append(pv)
        flags = holm_flags(pvals)
        for row, fl in zip(rows, flags):
            row["holm"] = fl
        rows.sort(key=lambda r: r["p"])
        cells[f"k{pool}_mean"] = {
            "mu": mu,
            "top": rows[:10],
            "n_holm_significant": sum(flags),
        }
    return cells


def run_one(day_masks, day_draws, game_cfg, label):
    """Construiește banca (cu semnale memoizate pe ACEASTĂ istorie), rulează
    ferestrele și evaluează celulele."""
    bank = build_bank(resolve=make_resolver())
    n_days = day_masks.shape[0]
    confirm_start = WARMUP_DAYS + int((n_days - WARMUP_DAYS) * (1 - CONFIRM_TAIL))
    windows = {
        "confirm30": range(confirm_start, n_days),
        "full100": range(WARMUP_DAYS, n_days),
    }
    pools = game_cfg["pools"]
    results = {}
    t0 = time.time()
    traj_cache: dict[str, np.ndarray] = {}
    for wname, days in windows.items():
        counts = {K: {} for K in pools}
        for name, fn in bank.items():
            if name not in traj_cache:
                traj_cache[name] = fn(day_masks)
            cell = count_hits(traj_cache[name], day_draws, pools, days)
            for K in pools:
                counts[K][name] = cell[K]
        results[wname] = evaluate_cell(counts, game_cfg["V"], game_cfg["k"], list(bank))
    print(f"  [{label}] {n_days} zile, {len(bank)} metode, {time.time()-t0:.0f}s", flush=True)
    return results


def main() -> None:
    probe = build_bank()
    print(f"Banca: {len(probe)} metode | jocuri: {list(GAMES)}", flush=True)
    print("Putere a priori (6/49, confirmare): pool6/5+ nevalidabil "
          "(0.014 așteptate); pool6/4+ cere ≥7; pool16/5+ cere ≥22.", flush=True)
    out = {"protocol": __doc__, "bank_size": len(probe), "games": {}}
    for game_key, cfg in GAMES.items():
        day_masks, day_draws, dpd = load_day_matrix(cfg["csv"], cfg["cols"], cfg["V"])
        print(f"{game_key}: {len(day_masks)} zile ({sum(dpd)} extrageri)", flush=True)
        entry = {"real": run_one(day_masks, day_draws, cfg, f"{game_key} real")}
        for seed in SHUFFLE_SEEDS:
            rng = np.random.default_rng(seed)
            perm = rng.permutation(len(day_masks))
            entry[f"shuffle_{seed}"] = run_one(
                day_masks[perm],
                [day_draws[i] for i in perm],
                cfg,
                f"{game_key} shuffle {seed}",
            )
        out["games"][game_key] = entry

    dest = ROOT / "scripts" / "analysis" / "wave4_pattern_screen_2026_11_09_results.json"
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    print(f"Rezultate: {dest}", flush=True)

    # Sumar: câte celule cu cel puțin un p Holm-semnificativ, real vs shuffle
    print("\n=== SUMAR Holm (real vs control) ===", flush=True)
    for game_key, entry in out["games"].items():
        for version, windows in entry.items():
            sig = sum(
                cell["n_holm_significant"]
                for w in windows.values()
                for cell in w.values()
            )
            print(f"  {game_key} [{version}]: semnificații Holm total = {sig}", flush=True)


if __name__ == "__main__":
    main()
