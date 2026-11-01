"""Enumerare exhaustivă a extragerilor — audit brute-force pe GPU opțional.

`enumerate_hit_histogram` parcurge TOATE cele C(max_num, draw_n) extrageri
uniforme (în batchuri, prin măști de biți) și numără, pentru fiecare extragere,
numărul maxim de hituri pe un bilet. Rezultatul e EXACT (întregi), la fel ca
numărătoarea combinatorială din `covering.probability`, dar independentă de
ea — servește ca verificare cruzată a ambelor.

GPU: dacă `cupy` e instalat (și `use_gpu` nu e False), batchurile se calculează
pe CUDA; altfel CPU numpy. În producție (venv CPU-only) calea e CPU; pe stația
cu RTX se poate rula auditul dintr-un venv cu cupy-cuda13x. Măsurat 2026-11-01
(RTX 5060 Ti): C(49,6) × 152 bilete = 6,8 s GPU vs 20 s CPU.

Nu se apelează din pipeline-ul de generare — acolo numărătoarea combinatorială
exactă ia ~1 ms la v=16. E doar pentru audit offline.
"""

from __future__ import annotations

from itertools import combinations
from math import comb

import numpy as np

_CUPY = None
try:  # opțional; producția rămâne CPU-only
    import cupy as _CUPY
except Exception:  # noqa: BLE001
    _CUPY = None


def gpu_available() -> bool:
    return _CUPY is not None


def _popcount(x, xp):
    if hasattr(xp, "bitwise_count"):
        return xp.bitwise_count(x)
    tbl = np.array([bin(i).count("1") for i in range(256)], dtype=np.uint8)
    if xp is not np:
        tbl = xp.asarray(tbl)
    return sum(
        tbl[(x >> xp.uint32(8 * k)).astype(xp.uint32) & xp.uint32(255)] for k in range(4)
    )


def enumerate_hit_histogram(
    pool,
    tickets,
    draw_n: int,
    max_num: int,
    batch: int = 1_000_000,
    use_gpu: bool | None = None,
) -> dict:
    """Histograma exactă: counts[h] = numărul de extrageri cu max-hit == h pe
    un bilet (h = 0..draw_n). `pool`/`tickets` pe numere 1..max_num."""
    pool = sorted(int(n) for n in pool)
    v = len(pool)
    if not (0 < draw_n <= max_num and v <= max_num):
        raise ValueError("geometrie invalidă")
    if any(not 1 <= n <= max_num for n in pool):
        raise ValueError("număr din pool în afara 1..max_num")
    pos = {n: i for i, n in enumerate(pool)}
    for t in tickets:
        if any(n not in pos for n in t):
            raise ValueError("bilet cu numere în afara pool-ului")
    tmasks = np.array(
        [sum(1 << pos[n] for n in t) for t in tickets], dtype=np.int64
    )
    xp = _CUPY if (use_gpu is None or use_gpu) and _CUPY is not None else np
    if xp is not np:
        tmasks = xp.asarray(tmasks)
    total = comb(max_num, draw_n)
    counts = np.zeros(draw_n + 1, dtype=np.int64)
    it = combinations(range(1, max_num + 1), draw_n)
    done = 0
    while True:
        chunk = []
        for _ in range(batch):
            try:
                chunk.append(next(it))
            except StopIteration:
                break
        if not chunk:
            break
        arr = np.array(chunk, dtype=np.int32)
        dmask = (np.int64(1) << (arr - 1)).sum(axis=1)
        if xp is not np:
            dm = xp.asarray(dmask)
            hits = _popcount(dm[:, None] & tmasks[None, :], xp).astype(xp.int64)
            mx = hits.max(axis=1)
            counts += xp.asnumpy(xp.bincount(mx, minlength=draw_n + 1))
        else:
            hits = _popcount(dmask[:, None] & tmasks[None, :], np).astype(np.int64)
            mx = hits.max(axis=1)
            counts += np.bincount(mx, minlength=draw_n + 1)
        done += len(chunk)
    if done != total:
        raise AssertionError(f"enumerare incompletă: {done} != {total}")
    probs = {
        t: float(counts[t:].sum()) / total for t in range(1, draw_n + 1)
    }
    return {
        "total_draws": total,
        "counts": counts.tolist(),
        "ticket_prob_ge": probs,
        "backend": "cupy" if xp is not np else "numpy",
    }


def verify_wheel_exact(pool, tickets, draw_n: int, max_num: int, use_gpu: bool | None = None) -> dict:
    """Verificare cruzată: enumerare exhaustivă vs `wheel_hit_probabilities`
    (numărătoare combinatorială). Returnează abaterea maximă (0 = identitate)."""
    from covering.probability import wheel_hit_probabilities

    enum = enumerate_hit_histogram(pool, tickets, draw_n, max_num, use_gpu=use_gpu)
    exact = wheel_hit_probabilities(pool, tickets, draw_n, max_num)["ticket"]
    diffs = {t: abs(enum["ticket_prob_ge"][t] - exact[t]) for t in range(1, draw_n + 1)}
    return {
        "backend": enum["backend"],
        "total_draws": enum["total_draws"],
        "max_abs_diff": max(diffs.values()),
        "diffs": diffs,
        "ok": max(diffs.values()) < 1e-12,
    }
