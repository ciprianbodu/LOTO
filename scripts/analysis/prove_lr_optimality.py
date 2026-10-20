"""Dovezi offline pentru pragurile la care o schemă LR nu atinge marginea superioară.

`covering.lr_schemes._upper_bound` e o margine: o schemă care o atinge e optimă la
acel prag, dar una care n-o atinge nu e neapărat sub maximul posibil. Pentru
fiecare schemă de cel mult 16 numere și fiecare prag sub margine, scriptul caută
maximul exact al numărătorului P(cel puțin t pe o variantă) peste toate
așezările cu tot atâtea variante distincte pe aceleași numere:

- căutare exhaustivă, cu primul bloc fixat (orice așezare se reetichetează așa
  încât să-l conțină, iar șansa nu depinde de etichete), când au cel mult
  ``--exhaustive`` așezări;
- altfel, la cel mult ``--ilp-max-n`` numere, programare liniară în numere
  întregi (HiGHS, `scipy.optimize.milp`, decalaj 0): x_B = 1 dacă blocul B e
  variantă, y_K <= suma x_B peste blocurile cu cel puțin t numere din K,
  maximizează suma ponderilor C(univers - n, extrase - k) pe y_K.

Un prag la care maximul găsit e chiar numărătorul schemei e scris ca optim
(``optimal``); unul la care s-a găsit o așezare mai bună e scris cu desfășurarea
ei (``improvable``), ca testul să o poată reverifica. Pragurile la care
aplicația dă deja mai mult cu tot atâtea variante (`app_same_size.json`) nu se
caută. Rezultatul: ``covering_designs/lr/optimal_offline.json``, citit de
`covering.lr_schemes.optimal_thresholds`.

    python scripts/analysis/prove_lr_optimality.py [--time-limit 900] [--out FIȘIER]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from itertools import combinations
from math import comb
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from covering.lr_schemes import (
    _GAMES,
    LR_DIR,
    _upper_bound,
    app_same_size,
    bound_thresholds,
    lr_schemes,
    scheme_numerators,
)


def _hit_sets(n: int, pick: int, t: int, draw_n: int, max_num: int):
    """Blocurile, k-submulțimile cu ponderea lor și, pe fiecare k-submulțime,
    blocurile care o prind (cel puțin t numere comune), ca măști de biți."""
    blocks = [sum(1 << i for i in c) for c in combinations(range(n), pick)]
    sets = []
    for k in range(t, min(n, draw_n) + 1):
        if draw_n - k > max_num - n:
            continue
        weight = comb(max_num - n, draw_n - k)
        for c in combinations(range(n), k):
            mask = sum(1 << i for i in c)
            sets.append((mask, weight))
    hits = [
        sum(1 << j for j, b in enumerate(blocks) if (b & mask).bit_count() >= t)
        for mask, _w in sets
    ]
    return blocks, sets, hits


def exhaustive(n, pick, size, t, draw_n, max_num):
    """Maximul exact și o așezare care îl atinge, pe toate așezările."""
    blocks, sets, hits = _hit_sets(n, pick, t, draw_n, max_num)
    best, arg = -1, None
    for rest in combinations(range(1, len(blocks)), size - 1):
        chosen = 1 | sum(1 << j for j in rest)
        num = sum(w for (_m, w), h in zip(sets, hits) if h & chosen)
        if num > best:
            best, arg = num, (0, *rest)
    return best, [blocks[j] for j in arg]


def ilp(n, pick, size, t, draw_n, max_num, time_limit):
    import numpy as np
    from scipy.optimize import Bounds, LinearConstraint, milp
    from scipy.sparse import lil_matrix

    blocks, sets, hits = _hit_sets(n, pick, t, draw_n, max_num)
    nb, nk = len(blocks), len(sets)
    c = np.concatenate([np.zeros(nb), -np.array([w for _m, w in sets], float)])
    a = lil_matrix((nk + 1, nb + nk))
    for i, h in enumerate(hits):
        a[i, nb + i] = 1
        for j in range(nb):
            if h >> j & 1:
                a[i, j] = -1
    a[nk, :nb] = 1
    lb = np.concatenate([np.full(nk, -np.inf), [size]])
    ub = np.concatenate([np.zeros(nk), [size]])
    lo = np.zeros(nb + nk)
    lo[0] = 1  # primul bloc fixat, ca la căutarea exhaustivă
    res = milp(
        c,
        constraints=LinearConstraint(a.tocsr(), lb, ub),
        integrality=np.concatenate([np.ones(nb), np.zeros(nk)]),
        bounds=Bounds(lo, np.ones(nb + nk)),
        options={"time_limit": float(time_limit), "disp": False, "mip_rel_gap": 0.0},
    )
    if res.x is None:
        return None, None, None, False
    chosen = [blocks[j] for j in range(nb) if res.x[j] > 0.5]
    num = sum(
        w for (_m, w), h in zip(sets, hits) if any(h >> j & 1 for j in range(nb) if res.x[j] > 0.5)
    )
    dual = -float(getattr(res, "mip_dual_bound", float("nan")))
    return num, chosen, dual, res.status == 0


def _rows(masks):
    return [[i + 1 for i in range(16) if m >> i & 1] for m in masks]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--exhaustive", type=int, default=200_000)
    ap.add_argument("--ilp-max-n", type=int, default=10)
    ap.add_argument("--time-limit", type=float, default=900.0)
    ap.add_argument("--out", type=Path, default=LR_DIR / "optimal_offline.json")
    args = ap.parse_args(argv)
    out = []
    for s in lr_schemes():
        if s.n > 16:
            continue
        _, pick, draw_n, max_num = _GAMES[s.game]
        numer = scheme_numerators(s)
        reached = set(bound_thresholds(s))
        app = [p for _g, v, p in app_same_size(s) if v == s.size]
        total = comb(max_num, draw_n)
        for t in sorted(numer):
            if t in reached:
                continue
            if any(p[t] * total > numer[t] + 0.5 for p in app):
                print(s.game, s.label, t, "aplicația dă mai mult: nu caut", flush=True)
                continue
            designs = comb(comb(s.n, pick) - 1, s.size - 1)
            t0 = time.time()
            if designs <= args.exhaustive:
                best, masks = exhaustive(s.n, pick, s.size, t, draw_n, max_num)
                method, proved = "exhaustive", True
                dual = best
            elif s.n <= args.ilp_max_n:
                best, masks, dual, proved = ilp(
                    s.n, pick, s.size, t, draw_n, max_num, args.time_limit
                )
                method = "ilp"
            else:
                print(s.game, s.label, t, "prea mare: nerezolvat", flush=True)
                continue
            seconds = round(time.time() - t0, 1)
            bound = _upper_bound(s.n, s.size, pick, t, draw_n, max_num) * total
            print(
                s.game, s.label, t, method, "schema", numer[t], "maxim", best,
                "dual", dual, "margine", round(bound, 1), "dovedit", proved, seconds, "s",
                flush=True,
            )
            if best is None:
                continue
            entry = {"game": s.game, "code": s.code, "t": t, "method": method,
                     "seconds": seconds, "scheme": numer[t]}
            if best > numer[t]:
                entry.update(status="improvable", best=best, rows=_rows(masks))
            elif proved and best == numer[t] and dual < numer[t] + 1:
                entry.update(status="optimal", maximum=best)
            else:
                continue
            out.append(entry)
    args.out.write_text(
        "[\n" + ",\n".join(json.dumps(e, ensure_ascii=False) for e in out) + "\n]\n",
        encoding="utf-8",
    )
    print("scris", args.out, len(out), "intrări")
    return 0


if __name__ == "__main__":
    sys.exit(main())
