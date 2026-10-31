"""Covere complete rafinate pentru pragul de peste garanție, prin ILP exact (HiGHS).

Pentru fiecare geometrie (joc, pool v, garanție g), rezolvă exact problema:

    max  P(>= g+1 pe un bilet)   s.t.  acoperirea g rămâne 100%, același număr
                                       de bilete, toate pozițiile jucate

P(>= t pe un bilet) = sum_k C(v,k) C(mx-v, draw-k) N_{k,t} / C(mx, draw), unde
N_{k,t} = numărul de k-submulțimi ale pool-ului care conțin o t-submulțime
acoperită (inclusă într-un bilet). Variabile binare x_t (bilet ales), y/z pentru
submulțimile acoperite; liniarizare standard. Obiectivul e exact, pe extragere
uniformă.

Rezultatul se certifică față de wheel-ul de producție cu regula
`covering.anneal_hits._certify` (același număr de bilete distincte, numerele
jucate rămân, niciun prag 3..extrase nu scade, câștig strict, acoperirea garanției
nu scade) și, dacă trece, se scrie
`covering_designs/refined/R_<v>_<pick>_<g>_<extrase>_<univers>.txt` (poziții 1..v).
Producția îl preia automat certificat (`covering.refined_covers`); cheia WF și a
worker-ului includ deja amprenta fișierului (`refined_cover_signature`), deci nu
e nevoie de bump de cache. Dacă ILP găsește doar egalul, geometria e dovedită
optimă la pragul g+1 și nu se scrie fișier.

Rulare din rădăcina proiectului:
    python scripts/analysis/gen_refined_covers_ilp.py [--time-limit S] [--games 6/49,5/40]
                                                       [--pools 9-16] [--guarantees 3,4]
                                                       [--jobs N] [--dry-run]
Raport: `scripts/analysis/refined_covers_ilp_<data>.json`.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from itertools import combinations
from math import comb
from pathlib import Path

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import lil_matrix

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from covering.anneal_hits import _certify  # noqa: E402
from covering.dispatch import generate_wheel  # noqa: E402
from covering.probability import wheel_hit_probabilities  # noqa: E402
from covering.refined_covers import refined_cover_path  # noqa: E402

GAMES = {"6/49": (49, 6, 6), "5/40": (40, 6, 5), "joker": (45, 5, 5)}  # univers, extrase, bilet
OUT_DIR = ROOT / "covering_designs" / "refined"


def build_and_solve(v: int, pick: int, g: int, draw: int, mx: int, size: int, time_limit: float):
    """Rezolvă ILP-ul și întoarce (bilete în poziții 0-based, info) sau (None, info)."""
    top = min(pick, draw)
    t_target = g + 1
    if t_target > top:
        return None, {"skip": "prag peste top (invariant)"}
    tickets = list(combinations(range(v), pick))
    t_idx = {t: i for i, t in enumerate(tickets)}
    nx = len(tickets)
    layers = [list(combinations(range(v), k)) for k in range(t_target, top + 1)]
    layer_idx = [{s: i for i, s in enumerate(layer)} for layer in layers]
    n_layers = [len(layer) for layer in layers]
    N = nx + sum(n_layers)
    offs = [nx]
    for n in n_layers[:-1]:
        offs.append(offs[-1] + n)

    rows: list[lil_matrix] = []
    lbs: list[float] = []
    ubs: list[float] = []

    def add_row(cols, lb, ub):
        r = lil_matrix((1, N))
        for c, val in cols:
            r[0, c] += val
        rows.append(r)
        lbs.append(lb)
        ubs.append(ub)

    add_row([(i, 1.0) for i in range(nx)], float(size), float(size))
    for q in combinations(range(v), g):
        qset = set(q)
        add_row([(t_idx[t], 1.0) for t in tickets if qset <= set(t)], 1.0, np.inf)
    for p in range(v):
        add_row([(t_idx[t], 1.0) for t in tickets if p in t], 1.0, np.inf)
    for f in layers[0]:
        fset = set(f)
        cols = [(offs[0] + layer_idx[0][f], 1.0)]
        cols += [(t_idx[t], -1.0) for t in tickets if fset <= set(t)]
        add_row(cols, -np.inf, 0.0)
    for li in range(1, len(layers)):
        prev = layers[li - 1]
        prev_idx = layer_idx[li - 1]
        for s in layers[li]:
            sset = set(s)
            cols = [(offs[li] + layer_idx[li][s], 1.0)]
            cols += [(offs[li - 1] + prev_idx[f], -1.0) for f in prev if set(f) <= sset]
            add_row(cols, -np.inf, 0.0)
            add_row([(offs[li] + layer_idx[li][s], 1.0)], -np.inf, 1.0)

    A = lil_matrix((len(rows), N))
    for i, r in enumerate(rows):
        A[i] = r
    A = A.tocsr()
    lb = np.array(lbs)
    ub = np.array(ubs)

    c = np.zeros(N)
    for li, layer in enumerate(layers):
        k = t_target + li
        w = comb(mx - v, draw - k) if draw - k >= 0 else 0
        for i in range(len(layer)):
            c[offs[li] + i] = -float(w)

    t0 = time.time()
    res = milp(
        c,
        constraints=LinearConstraint(A, lb, ub),
        integrality=np.ones(N),
        bounds=Bounds(0, 1),
        options={"time_limit": time_limit, "mip_rel_gap": 0.0, "presolve": True},
    )
    info = {
        "status": int(res.status),
        "message": str(res.message),
        "seconds": round(time.time() - t0, 1),
        "vars": N,
        "constraints": int(A.shape[0]),
        "obj": float(-res.fun) if res.fun is not None else None,
    }
    if res.x is None:
        return None, info
    sel = [tickets[i] for i in range(nx) if res.x[i] > 0.5]
    if len(sel) != size:
        info["warn"] = f"selected {len(sel)} != {size}"
    return sel, info


def run_one(game: str, v: int, g: int, time_limit: float, dry_run: bool = False):
    mx, draw, pick = GAMES[game]
    pool = list(range(1, v + 1))
    scores = {n: float(v - i) for i, n in enumerate(pool)}
    prod, cov = generate_wheel("lajolla", pool, pick, g, 0, scores, None, draw, mx)
    prod = [sorted(int(n) for n in t) for t in prod]
    size = len(prod)
    before = wheel_hit_probabilities(pool, prod, draw, mx)["ticket"]
    sel, info = build_and_solve(v, pick, g, draw, mx, size, time_limit)
    info.update(game=game, v=v, g=g, size=size, cov=cov)
    if sel is None:
        info["result"] = info.get("skip", "no solution")
        return info
    candidate = [sorted(p + 1 for p in t) for t in sel]
    candidate_nums = [sorted(pool[p - 1] for p in t) for t in sel]
    why, _gain, _cov_after = _certify(pool, prod, candidate_nums, g, draw, mx)
    after = wheel_hit_probabilities(pool, candidate_nums, draw, mx)["ticket"]
    info["certify"] = why or "ok"
    info["gain"] = {str(k): after[k] - before[k] for k in sorted(after)}
    info["before"] = {str(k): before[k] for k in sorted(before)}
    info["after"] = {str(k): after[k] for k in sorted(after)}
    if why:
        info["result"] = f"not certified: {why}"
        return info
    info["result"] = "certified gain"
    if not dry_run:
        name = refined_cover_path(v, pick, g, draw, mx)
        path = OUT_DIR / name
        path.write_text(
            "".join(" ".join(map(str, t)) + "\n" for t in candidate), encoding="ascii"
        )
        info["file"] = str(path.relative_to(ROOT))
    return info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--time-limit", type=float, default=600.0)
    ap.add_argument("--games", default="6/49,5/40,joker")
    ap.add_argument("--pools", default="7-16")
    ap.add_argument("--guarantees", default="3,4")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    a, b = args.pools.split("-")
    pools = range(int(a), int(b) + 1)
    guarantees = [int(x) for x in args.guarantees.split(",")]
    games = [g for g in args.games.split(",") if g in GAMES]

    tasks = []
    for game in games:
        _mx, _draw, pick = GAMES[game]
        for g in guarantees:
            if g >= pick:
                continue
            for v in pools:
                if v <= pick:
                    continue
                tasks.append((game, v, g))

    results = []
    with ProcessPoolExecutor(max_workers=args.jobs) as ex:
        futs = {ex.submit(run_one, *t, args.time_limit, args.dry_run): t for t in tasks}
        for fut in as_completed(futs):
            info = fut.result()
            results.append(info)
            print(
                f"{info['game']} v={info['v']} g={info['g']} size={info['size']} "
                f"-> {info['result']} ({info.get('seconds')}s)"
            )
            sys.stdout.flush()

    results.sort(key=lambda r: (r["game"], r["v"], r["g"]))
    stamp = time.strftime("%Y-%m-%d")
    report = ROOT / "scripts" / "analysis" / f"refined_covers_ilp_{stamp}.json"
    report.write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
    print(f"raport: {report}")


if __name__ == "__main__":
    main()
