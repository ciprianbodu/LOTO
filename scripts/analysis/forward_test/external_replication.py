"""Replicarea `dmd_forecast` (copia înghețată) pe istoricele altor loterii 6/49.

Metoda nu a fost construită și nici aleasă pe aceste extrageri, deci fiecare
extragere de aici e în afara eșantionului. Pentru fiecare extragere, începând cu
a 201-a a fiecărei loterii (fereastra metodei are 200 de extrageri), pool-ul se
calculează numai din extragerile cu dată anterioară.

Analiza principală (fixată înainte de rezultate, vezi PREREGISTRATION.md): toate
loteriile verificate la un loc, reușită = 3+ numere extrase în pool, test binomial
exact unilateral față de rata hipergeometrică, pentru pool 6 și pool 12, cu
α = 0,0125 fiecare. Rezultatele pe fiecare loterie sunt secundare.

Rulare din rădăcina proiectului:
    python scripts/analysis/forward_test/external_replication.py CSV [CSV ...]
Fiecare CSV: date,n1..n6, în formatul din `_ISTORIC/` (ZZ-LL-AAAA sau AAAA-LL-ZZ).
Datele se citesc și se validează ca în `forward_test.load_history`: un rând
nevalid, repetat sau cu dată necunoscută oprește rularea.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
from scipy.stats import binomtest, hypergeom

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import forward_test  # noqa: E402
import frozen_dmd  # noqa: E402

POOLS = (6, 12)
TARGET = 3
ALPHA = 0.0125  # per pool: verdictul pe două pool-uri rămâne la cel mult 2,5%
WARMUP = 200


def load(path: Path):
    """Aceeași citire ca la testul pe extrageri viitoare: date calendaristice,
    ordine cronologică, validare 6 numere distincte în 1..49."""
    return forward_test.load_history(path, max_num=49, draw_n=6)


def replicate(dates, draws: np.ndarray) -> dict:
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
    ci = binomtest(s, n, p0).proportion_ci(0.95, method="exact") if n else None
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
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
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
            "first": dates[0].isoformat(),
            "last": dates[-1].isoformat(),
            "sha256": forward_test.file_hash(p),  # pe LF, ca pe orice checkout
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
