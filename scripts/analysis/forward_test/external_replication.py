"""Replicarea `dmd_forecast` (copia înghețată) pe istoricele altor loterii 6/49.

Metoda nu a fost construită și nici aleasă pe aceste extrageri, deci fiecare
extragere de aici e în afara eșantionului. Pentru fiecare extragere, începând cu
a 201-a a fiecărei loterii (fereastra metodei are 200 de extrageri), pool-ul se
calculează numai din extragerile cu dată anterioară.

Analiza principală (fixată înainte de rezultate, vezi PREREGISTRATION.md): toate
loteriile verificate la un loc, reușită = 3+ numere extrase în pool, test binomial
exact unilateral față de rata hipergeometrică, pentru pool 6 și pool 12, cu
α = 0,025 fiecare. Rezultatele pe fiecare loterie sunt secundare.

Rulare din rădăcina proiectului:
    python scripts/analysis/forward_test/external_replication.py DIR_CU_CSV [...]
Fiecare CSV: date (YYYY-MM-DD),n1..n6, cronologic. Datele nu se comit în repo.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np
from scipy.stats import binomtest, hypergeom

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import frozen_dmd  # noqa: E402

POOLS = (6, 12)
TARGET = 3
ALPHA = 0.025
WARMUP = 200


def load(path: Path) -> tuple[list[str], np.ndarray]:
    dates, rows = [], []
    for line in path.read_text(encoding="utf-8").splitlines()[1:]:
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split(",")]
        nums = [int(x) for x in parts[1:7]]
        if len(set(nums)) != 6 or not all(1 <= x <= 49 for x in nums):
            raise ValueError(f"{path.name}: rând invalid {line!r}")
        dates.append(parts[0])
        rows.append(nums)
    order = sorted(range(len(dates)), key=lambda i: (dates[i], i))
    return [dates[i] for i in order], np.asarray([rows[i] for i in order], dtype=int)


def replicate(dates: list[str], draws: np.ndarray) -> dict:
    hits = {k: [] for k in POOLS}
    prior = 0
    for i in range(len(draws)):
        while prior < i and dates[prior] < dates[i]:
            prior += 1
        if prior < WARMUP:
            continue
        rk = frozen_dmd.ranking(draws[:prior], 49)
        drawn = set(int(x) for x in draws[i])
        for k in POOLS:
            hits[k].append(len(drawn & set(rk[:k])))
    return {k: np.asarray(v) for k, v in hits.items()}


def summarize(h: np.ndarray, k: int) -> dict:
    p0 = float(hypergeom(49, k, 6).sf(TARGET - 1))
    n = int(h.size)
    s = int((h >= TARGET).sum())
    test = binomtest(s, n, p0, alternative="greater") if n else None
    ci = test.proportion_ci(0.95) if n else None
    return {
        "n": n,
        "success": s,
        "rate": s / n if n else float("nan"),
        "random_rate": p0,
        "p_value": test.pvalue if n else float("nan"),
        "ci95": [ci.low, ci.high] if n else [float("nan")] * 2,
        "mean_hits": float(h.mean()) if n else float("nan"),
        "random_mean_hits": k * 6 / 49,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("csvs", nargs="+")
    ap.add_argument("--json", default="")
    args = ap.parse_args(argv)
    per, pooled = {}, {k: [] for k in POOLS}
    for p in map(Path, args.csvs):
        dates, draws = load(p)
        h = replicate(dates, draws)
        per[p.stem] = {
            "rows": len(draws),
            "first": dates[0],
            "last": dates[-1],
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
            **{f"pool{k}": summarize(h[k], k) for k in POOLS},
        }
        for k in POOLS:
            pooled[k].append(h[k])
    out = {
        "per_lottery": per,
        "pooled": {f"pool{k}": summarize(np.concatenate(pooled[k]), k) for k in POOLS},
        "alpha_each": ALPHA,
    }
    for name, r in {**per, "TOATE": out["pooled"]}.items():
        cells = []
        for k in POOLS:
            x = r[f"pool{k}"]
            cells.append(
                f"pool {k}: {x['success']}/{x['n']} = {100 * x['rate']:.2f}% "
                f"(aleator {100 * x['random_rate']:.2f}%, p={x['p_value']:.3f})"
            )
        print(f"{name:18s} " + " | ".join(cells))
    if args.json:
        Path(args.json).write_text(json.dumps(out, indent=1), encoding="utf-8")
    verdict = [
        k for k in POOLS if out["pooled"][f"pool{k}"]["p_value"] < ALPHA
    ]
    print(
        "Analiza principală: "
        + ("avantaj pe pool " + ", ".join(map(str, verdict)) if verdict else "niciun avantaj față de aleator")
    )
    return 0 if all(math.isfinite(out["pooled"][f"pool{k}"]["rate"]) for k in POOLS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
