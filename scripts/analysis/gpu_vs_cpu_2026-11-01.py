"""Benchmark CPU (numpy) vs GPU (CuPy/CUDA) pe operațiile reale din proiect,
2026-11-01. Rulat cu un venv care are și cupy-cuda13x; fără cupy, rulează doar
CPU și raportează că GPU lipsește (nu e eroare).

Ce măsurăm (toate sunt căi reale din pipeline):
  A small_ops   bucla de scoreri: 1000 de iterații de operații mici pe (2600, 49)
                float64 (media pe coloane, matmul cu vector, exp) — modelul
                "apel scorer per zi" din bench/WF. GPU măsurat și cu datele
                rezident (fără transfer la fiecare apel).
  B mi_window   MI întârziată: 2600 de zile × matmul (500×49)ᵀ@(500×49).
  C hit_counts  numărările întregi din covering.probability._hit_counts la
                v=16 (2^16) și v=20 (2^20): închidere familie + propagare max +
                popcount + bincount.
  D enumerate   enumerarea exhaustivă a tuturor C(49,6)=13.983.816 extrageri
                vs 152 de bilete (numărarea hiturilor prin măști de biți) —
                singurul workload mare din proiect; în producție se folosește
                numărătoarea combinatorială exactă, mai rapidă.
  E big_matmul  4000×4000 @ 4000×4000 — cazul în care GPU câștigă clar, dar
                pipeline-ul nu are astfel de operații dense.

Concluzia așteptată: GPU câștigă doar la D/E (batch mare), pierde la A/B/C
(overhead de transfer și kernel-launch pe operații mici). Rulare:
  python scripts/analysis/gpu_vs_cpu_2026-11-01.py
"""

from __future__ import annotations

import json
import time
from itertools import combinations
from math import comb
from pathlib import Path

import numpy as np

OUT = Path(__file__).resolve().parent / "gpu_vs_cpu_2026-11-01.json"

try:
    import cupy as cp

    GPU = True
except Exception:
    cp = None
    GPU = False


def _popcount(x):
    """popcount pe uint32, numpy sau cupy."""
    xp = cp if (GPU and isinstance(x, cp.ndarray)) else np
    if hasattr(xp, "bitwise_count"):
        return xp.bitwise_count(x)
    # fallback: tabel pe octet
    tbl = np.array([bin(i).count("1") for i in range(256)], dtype=np.uint8)
    if xp is cp:
        tbl = cp.asarray(tbl)
    return sum(
        tbl[(x >> np.uint32(8 * k)).astype(np.uint32) & np.uint32(255)] for k in range(4)
    )


def bench_small_ops(xp, n=2600, N=49, iters=1000):
    rng = np.random.default_rng(0)
    X = rng.random((n, N))
    w = rng.random(N)
    if xp is cp:
        X, w = cp.asarray(X), cp.asarray(w)
    t0 = time.perf_counter()
    for _ in range(iters):
        m = X.mean(axis=0)
        y = X @ w
        z = xp.exp(m) + y.sum()
    if xp is cp:
        cp.cuda.Device().synchronize()
    return (time.perf_counter() - t0) / iters * 1e6  # µs per iter


def bench_small_ops_transfer(iters=1000):
    """Varianta GPU cu transfer H2D la fiecare apel (modelul real: datele vin
    din istoric pe CPU)."""
    rng = np.random.default_rng(0)
    X = rng.random((2600, 49))
    w = rng.random(49)
    t0 = time.perf_counter()
    for _ in range(iters):
        Xg = cp.asarray(X)
        wg = cp.asarray(w)
        z = cp.exp(Xg.mean(axis=0)).sum() + (Xg @ wg).sum()
        float(z)
    cp.cuda.Device().synchronize()
    return (time.perf_counter() - t0) / iters * 1e6


def bench_mi_window(xp, days=2600, W=500, N=49):
    rng = np.random.default_rng(1)
    Ib = (rng.random((days, N)) < 6 / N).astype(np.float64)
    if xp is cp:
        Ib = cp.asarray(Ib)
    t0 = time.perf_counter()
    acc = 0.0
    for d in range(W, days):
        A = Ib[d - W:d]
        acc += float((A.T @ A).sum())
    if xp is cp:
        cp.cuda.Device().synchronize()
    return time.perf_counter() - t0, acc


def _hit_counts(xp, v, masks):
    size = 1 << v
    family = xp.zeros(size, dtype=bool)
    family[xp.asarray(masks, dtype=xp.int64)] = True
    for i in range(v):
        view = family.reshape(-1, 2, 1 << i)
        view[:, 0, :] |= view[:, 1, :]
    pop = _popcount(xp.arange(size, dtype=xp.uint32)).astype(xp.int8)
    best = xp.where(family, pop, 0).astype(xp.int8)
    for i in range(v):
        view = best.reshape(-1, 2, 1 << i)
        xp.maximum(view[:, 1, :], view[:, 0, :], out=view[:, 1, :])
    hist = xp.bincount(
        pop.astype(xp.int64) * (v + 1) + best.astype(xp.int64),
        minlength=(v + 1) * (v + 1),
    ).reshape(v + 1, v + 1)
    return hist


def bench_hit_counts(xp, v, reps=5):
    rng = np.random.default_rng(2)
    masks = [int(sum(1 << int(x) for x in rng.choice(v, 6, replace=False))) for _ in range(152)]
    t0 = time.perf_counter()
    for _ in range(reps):
        h = _hit_counts(xp, v, masks)
    if xp is cp:
        cp.cuda.Device().synchronize()
    return (time.perf_counter() - t0) / reps * 1e3, int(h.sum())  # ms


def bench_enumerate(xp, batch=1_000_000):
    """Toate C(49,6) extrageri vs 152 bilete, prin măști de biți."""
    rng = np.random.default_rng(3)
    tickets = [tuple(sorted(rng.choice(49, 6, replace=False) + 1)) for _ in range(152)]
    tmasks = np.array([sum(1 << (t - 1) for t in tk) for tk in tickets], dtype=np.int64)
    if xp is cp:
        tmasks = cp.asarray(tmasks)
    counts = np.zeros(7, dtype=np.int64)
    t0 = time.perf_counter()
    done = 0
    # exhaustiv, în batchuri din iteratorul de combinații
    it = combinations(range(49), 6)
    while True:
        batch_list = []
        for _ in range(batch):
            try:
                batch_list.append(next(it))
            except StopIteration:
                break
        if not batch_list:
            break
        arr = np.array(batch_list, dtype=np.int32)  # (b, 6) poziții 0..48
        dmask = (np.int64(1) << arr).sum(axis=1)
        if xp is cp:
            dmask = cp.asarray(dmask)
            tm = tmasks
            hits = _popcount((dmask[:, None] & tm[None, :])).astype(cp.int64)
            mx = hits.max(axis=1)
            cnt = cp.bincount(mx, minlength=7)
            counts += cp.asnumpy(cnt)
        else:
            hits = _popcount((dmask[:, None] & tmasks[None, :])).astype(np.int64)
            mx = hits.max(axis=1)
            counts += np.bincount(mx, minlength=7)
        done += len(batch_list)
    return time.perf_counter() - t0, done, counts


def bench_big_matmul(xp, n=4000, reps=3):
    rng = np.random.default_rng(4)
    A = rng.random((n, n))
    if xp is cp:
        A = cp.asarray(A)
    t0 = time.perf_counter()
    for _ in range(reps):
        C = A @ A
    if xp is cp:
        cp.cuda.Device().synchronize()
    return (time.perf_counter() - t0) / reps * 1e3, float(C.sum())  # ms


def main() -> None:
    res = {"gpu_available": GPU, "benchmarks": {}}
    if GPU:
        res["gpu_name"] = cp.cuda.Device().attributes.get("Name", "?") if hasattr(cp.cuda.Device(), "attributes") else str(cp.cuda.Device())
    res["benchmarks"]["A_small_ops_cpu_us"] = bench_small_ops(np)
    res["benchmarks"]["B_mi_window_cpu_s"] = bench_mi_window(np)[0]
    res["benchmarks"]["C_hit_counts_v16_cpu_ms"] = bench_hit_counts(np, 16)[0]
    res["benchmarks"]["C_hit_counts_v20_cpu_ms"] = bench_hit_counts(np, 20, reps=2)[0]
    res["benchmarks"]["E_big_matmul_cpu_ms"] = bench_big_matmul(np, reps=1)[0]
    if GPU:
        res["benchmarks"]["A_small_ops_gpu_resident_us"] = bench_small_ops(cp)
        res["benchmarks"]["A_small_ops_gpu_transfer_us"] = bench_small_ops_transfer()
        res["benchmarks"]["B_mi_window_gpu_s"] = bench_mi_window(cp)[0]
        res["benchmarks"]["C_hit_counts_v16_gpu_ms"] = bench_hit_counts(cp, 16)[0]
        res["benchmarks"]["C_hit_counts_v20_gpu_ms"] = bench_hit_counts(cp, 20, reps=2)[0]
        res["benchmarks"]["E_big_matmul_gpu_ms"] = bench_big_matmul(cp)[0]
        t, done, counts = bench_enumerate(cp)
        res["benchmarks"]["D_enumerate_gpu_s"] = t
        res["benchmarks"]["D_enumerate_draws"] = done
        res["benchmarks"]["D_enumerate_hit_hist"] = counts.tolist()
    t, done, counts = bench_enumerate(np)
    res["benchmarks"]["D_enumerate_cpu_s"] = t
    res["benchmarks"]["D_enumerate_draws"] = done
    res["benchmarks"]["D_enumerate_hit_hist"] = counts.tolist()
    OUT.write_text(json.dumps(res, indent=1, default=float), encoding="utf-8")
    for k, v in res["benchmarks"].items():
        print(f"{k:38s} {v if not isinstance(v, float) else round(v, 3)}", flush=True)
    print(f"-> {OUT.name}", flush=True)


if __name__ == "__main__":
    main()
