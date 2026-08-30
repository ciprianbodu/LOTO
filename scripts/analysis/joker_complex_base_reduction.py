#!/usr/bin/env python3
"""Test COMPLEX de reducere a bazei — DOAR Joker.

Față de pasul 1–3 din README (constrângeri STATICE, autocorelație, pool aleator):
  • măști ADAPTIVE (depind de ultimele W extrageri, nu de o bază fixă);
  • urna 1 (5/45) ȘI urna 2 (1/20);
  • combinații AND între măști care se declanșează des;
  • descoperire pe primele 70% + confirmare walk-forward pe ultimele 30%
    (pool 11 / rank_by_score / frequency-în-mască vs frequency);
  • test de permutare pe liftul de acoperire (Bonferroni pe nr. de măști).

Nu e producție. Nu importă engine-ul. Rulează din rădăcina repo-ului:

    python scripts/analysis/joker_complex_base_reduction.py

Un scorer se justifică DOAR dacă o mască: (1) lift de acoperire >1 cu p
ajustat < 0.05 pe descoperire, (2) bate frequency la 3+ pe confirmare,
(3) |Spearman| < 0.95 vs frequency, (4) nu e bloc consecutiv.

Rezultat măsurat 2026-08-30 (n=2181, disc=1526, confirm=655):
  U1: 195 atomice + 66 AND; 0 supraviețuitori Bonferroni (α=0.00019).
      Pe confirmare, top-ul e clonă de frequency (|ρ|=1.00) sau pierde la 3+.
  U2: 80 atomice; 0 Bonferroni (α=0.00063). ``u2_hot_W5`` pare +7 hit@1 vs
      frequency dar 4.27% < 5% random, hit@3 = 15.3% = 3/20, p_disc=0.38 —
      e recency pe 5 valori, nu un tipar.
"""
from __future__ import annotations

import csv
import math
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from loto_enterprise.benchmark.methods import score_frequency  # noqa: E402
from loto_enterprise.core.ranking import is_consecutive_block, rank_by_score  # noqa: E402

CSV = ROOT / "_ISTORIC" / "joker.csv"
N1, K1 = 45, 5
N2 = 20
SPLIT = 0.70
POOL = 11
BLOCK = 8
MIN_FIRE = 40
MIN_SAVE = 4
NPERM = 400
TOP_CONFIRM = 18
RNG = np.random.default_rng(20260830)


def load():
    u1, u2 = [], []
    with CSV.open(newline="", encoding="utf-8") as f:
        for rec in csv.DictReader(f):
            row = [int(rec[f"n{i}"]) for i in range(1, 6)]
            j = int(rec["joker"])
            if len(set(row)) == 5 and all(1 <= v <= N1 for v in row) and 1 <= j <= N2:
                u1.append(row)
                u2.append(j)
    return np.asarray(u1, dtype=np.int64), np.asarray(u2, dtype=np.int64)


def bits_from_nums(nums, nmax: int) -> int:
    b = 0
    for v in nums:
        if 1 <= int(v) <= nmax:
            b |= 1 << (int(v) - 1)
    return b


def mask_to_bits(mask: np.ndarray) -> int:
    b = 0
    for i, v in enumerate(mask):
        if v:
            b |= 1 << i
    return int(b)


def popcount(x: int) -> int:
    return int(x).bit_count()


def coverage(mask_bits: int, next_bits: int, k: int) -> float:
    return popcount(mask_bits & next_bits) / k


# ---------------------------------------------------------------------------
# Precompute window features
# ---------------------------------------------------------------------------

def gaps_matrix(draws: np.ndarray, nmax: int) -> np.ndarray:
    """gaps[t, k-1] = extrageri de la ultima apariție a lui k, înainte de t."""
    n = len(draws)
    last = np.full(nmax + 1, -1, dtype=np.int32)
    out = np.zeros((n, nmax), dtype=np.int32)
    for t in range(n):
        for k in range(1, nmax + 1):
            out[t, k - 1] = t - last[k] if last[k] >= 0 else t + 1
        for v in np.atleast_1d(draws[t]):
            last[int(v)] = t
    return out


def window_union_bits(draws: np.ndarray, W: int, nmax: int) -> np.ndarray:
    n = len(draws)
    out = np.zeros(n, dtype=np.int64)
    for t in range(n):
        lo = max(0, t - W)
        out[t] = bits_from_nums(draws[lo:t].ravel(), nmax) if t else 0
    return out


# ---------------------------------------------------------------------------
# Mask catalog (adaptive). Each builder: t -> bits or 0 meaning "no fire"
# FULL universe bits = (1<<N)-1. "No fire" returns FULL (no reduction).
# ---------------------------------------------------------------------------

def full_bits(nmax: int) -> int:
    return (1 << nmax) - 1


def catalog_urna1(draws: np.ndarray, gaps: np.ndarray):
    n = len(draws)
    mins = draws.min(axis=1)
    maxs = draws.max(axis=1)
    even = (draws % 2 == 0)
    FULL = full_bits(N1)
    nums = np.arange(1, N1 + 1)
    even_bits = mask_to_bits(nums % 2 == 0)
    odd_bits = mask_to_bits(nums % 2 == 1)

    def decade_of(x):
        return (x - 1) // 10

    dec_bits = []
    for d in range(5):
        dec_bits.append(mask_to_bits((decade_of(nums) == d)))
    digit_bits = [mask_to_bits(nums % 10 == d) for d in range(10)]
    mod_bits = {}
    for m in (3, 5, 7, 9):
        mod_bits[m] = [mask_to_bits(nums % m == r) for r in range(m)]

    builders = []

    def add(name, fn):
        builders.append((name, fn))

    for W in (1, 2, 3, 5, 8):
        def cap_fn(T, W=W):
            def fn(t):
                if t < W:
                    return FULL
                if int(maxs[t - W:t].max()) <= T:
                    return (1 << T) - 1  # numbers 1..T
                return FULL
            return fn
        for T in (30, 32, 35, 38, 40, 42):
            add(f"cap{T}_W{W}", cap_fn(T))

        def floor_fn(T, W=W):
            def fn(t):
                if t < W:
                    return FULL
                if int(mins[t - W:t].min()) >= T:
                    return FULL ^ ((1 << (T - 1)) - 1)  # drop 1..T-1
                return FULL
            return fn
        for T in (3, 5, 8, 10, 12):
            add(f"floor{T}_W{W}", floor_fn(T))

        def env_fn(margin, W=W):
            def fn(t):
                if t < W:
                    return FULL
                lo = max(1, int(mins[t - W:t].min()) - margin)
                hi = min(N1, int(maxs[t - W:t].max()) + margin)
                return ((1 << hi) - 1) ^ ((1 << (lo - 1)) - 1)
            return fn
        for margin in (0, 1, 2):
            add(f"env_m{margin}_W{W}", env_fn(margin))

        def majcap_fn(T, frac, W=W):
            def fn(t):
                if t < W:
                    return FULL
                if float((draws[t - W:t] <= T).mean()) >= frac:
                    return (1 << T) - 1
                return FULL
            return fn
        for T, frac in ((35, 0.90), (40, 0.85), (40, 0.90)):
            add(f"majcap{T}_f{frac}_W{W}", majcap_fn(T, frac))

        def parity_fn(want_even, thresh, W=W):
            def fn(t):
                if t < W:
                    return FULL
                fr = float(even[t - W:t].mean())
                if want_even and fr >= thresh:
                    return even_bits
                if (not want_even) and fr <= (1.0 - thresh):
                    return odd_bits
                return FULL
            return fn
        for th in (0.75, 0.85, 1.0):
            add(f"even_lock_{th}_W{W}", parity_fn(True, th))
            add(f"odd_lock_{th}_W{W}", parity_fn(False, th))

        def occ_keep(kind, W=W):
            def fn(t):
                if t < W:
                    return FULL
                flat = draws[t - W:t].ravel()
                keep = 0
                if kind == "decade":
                    seen = {decade_of(int(v)) for v in flat}
                    for d in seen:
                        keep |= dec_bits[d]
                elif kind == "digit":
                    seen = {int(v) % 10 for v in flat}
                    for d in seen:
                        keep |= digit_bits[d]
                return keep if keep else FULL
            return fn
        add(f"keep_hot_decades_W{W}", occ_keep("decade"))
        add(f"keep_hot_digits_W{W}", occ_keep("digit"))

        def drop_mod_unused(m, W=W):
            def fn(t):
                if t < W:
                    return FULL
                flat = draws[t - W:t].ravel()
                seen = {int(v) % m for v in flat}
                keep = 0
                for r in seen:
                    keep |= mod_bits[m][r]
                return keep if keep else FULL
            return fn
        for m in (3, 5, 7):
            add(f"keep_hot_mod{m}_W{W}", drop_mod_unused(m))

        def neigh_fn(D, W=W):
            def fn(t):
                if t < 1:
                    return FULL
                src = draws[max(0, t - W):t].ravel()
                keep = 0
                for v in src:
                    lo = max(1, int(v) - D)
                    hi = min(N1, int(v) + D)
                    for x in range(lo, hi + 1):
                        keep |= 1 << (x - 1)
                return keep if keep else FULL
            return fn
        for D in (3, 5, 8):
            add(f"neighbors_d{D}_W{W}", neigh_fn(D))

        def hot_union(W=W):
            def fn(t):
                if t < W:
                    return FULL
                b = bits_from_nums(draws[t - W:t].ravel(), N1)
                return b if b else FULL
            return fn
        add(f"hot_union_W{W}", hot_union())

        def drop_union(W=W):
            def fn(t):
                if t < W:
                    return FULL
                b = bits_from_nums(draws[t - W:t].ravel(), N1)
                return (FULL ^ b) if b else FULL
            return fn
        add(f"drop_last_W{W}", drop_union())

        def overdue(G, W=W):
            def fn(t):
                if t < W:
                    return FULL
                keep = 0
                for k in range(N1):
                    if int(gaps[t, k]) >= G:
                        keep |= 1 << k
                return keep if popcount(keep) >= POOL else FULL
            return fn
        for G in (10, 18, 30):
            add(f"overdue_g{G}_W{W}", overdue(G))

        def recent_gap(G, W=W):
            def fn(t):
                if t < W:
                    return FULL
                keep = 0
                for k in range(N1):
                    if int(gaps[t, k]) <= G:
                        keep |= 1 << k
                return keep if popcount(keep) >= POOL else FULL
            return fn
        for G in (5, 10, 20):
            add(f"recent_g{G}_W{W}", recent_gap(G))

    # Pairwise AND of a small sticky subset (computed later from bits)
    return builders


def catalog_urna2(jokers: np.ndarray, gaps: np.ndarray):
    n = len(jokers)
    FULL = full_bits(N2)
    nums = np.arange(1, N2 + 1)
    even_bits = mask_to_bits(nums % 2 == 0)
    odd_bits = mask_to_bits(nums % 2 == 1)
    builders = []

    def add(name, fn):
        builders.append((name, fn))

    for W in (1, 2, 3, 5, 8):
        def cap_fn(T, W=W):
            def fn(t):
                if t < W:
                    return FULL
                if int(jokers[t - W:t].max()) <= T:
                    return (1 << T) - 1
                return FULL
            return fn
        for T in (8, 10, 12, 15):
            add(f"u2_cap{T}_W{W}", cap_fn(T))

        def floor_fn(T, W=W):
            def fn(t):
                if t < W:
                    return FULL
                if int(jokers[t - W:t].min()) >= T:
                    return FULL ^ ((1 << (T - 1)) - 1)
                return FULL
            return fn
        for T in (6, 8, 11):
            add(f"u2_floor{T}_W{W}", floor_fn(T))

        def even_lock(W=W):
            def fn(t):
                if t < W:
                    return FULL
                w = jokers[t - W:t]
                if np.all(w % 2 == 0):
                    return even_bits
                if np.all(w % 2 == 1):
                    return odd_bits
                return FULL
            return fn
        add(f"u2_parity_lock_W{W}", even_lock())

        def neigh(D, W=W):
            def fn(t):
                if t < 1:
                    return FULL
                keep = 0
                for v in jokers[max(0, t - W):t]:
                    lo = max(1, int(v) - D)
                    hi = min(N2, int(v) + D)
                    for x in range(lo, hi + 1):
                        keep |= 1 << (x - 1)
                return keep if keep else FULL
            return fn
        for D in (1, 2, 3):
            add(f"u2_neigh{D}_W{W}", neigh(D))

        def hot(W=W):
            def fn(t):
                if t < W:
                    return FULL
                b = bits_from_nums(jokers[t - W:t], N2)
                return b if popcount(b) >= 3 else FULL
            return fn
        add(f"u2_hot_W{W}", hot())

        def cold(W=W):
            def fn(t):
                if t < W:
                    return FULL
                b = bits_from_nums(jokers[t - W:t], N2)
                return (FULL ^ b) if popcount(FULL ^ b) >= 3 else FULL
            return fn
        add(f"u2_drop_last_W{W}", cold())

        def overdue(G, W=W):
            def fn(t):
                if t < W:
                    return FULL
                keep = 0
                for k in range(N2):
                    if int(gaps[t, k]) >= G:
                        keep |= 1 << k
                return keep if popcount(keep) >= 3 else FULL
            return fn
        for G in (8, 12, 18):
            add(f"u2_overdue_g{G}_W{W}", overdue(G))

    return builders


def eval_discovery(builders, next_bits: np.ndarray, nmax: int, k: int, t0: int, t1: int):
    FULL = full_bits(nmax)
    rows = []
    series = {}
    for name, fn in builders:
        lifts = []
        sizes = []
        covs = []
        n_fire = 0
        mask_at = np.zeros(t1, dtype=np.int64)
        for t in range(t0, t1):
            m = int(fn(t))
            mask_at[t] = m
            sz = popcount(m)
            cov = coverage(m, int(next_bits[t]), k)
            if m != FULL and sz <= nmax - MIN_SAVE:
                n_fire += 1
                lifts.append(cov / max(sz / nmax, 1e-9))
                sizes.append(sz)
                covs.append(cov)
        if n_fire < MIN_FIRE:
            continue
        mean_lift = float(np.mean(lifts))
        rows.append({
            "name": name,
            "n_fire": n_fire,
            "fire_pct": 100.0 * n_fire / (t1 - t0),
            "mean_size": float(np.mean(sizes)),
            "mean_cov": float(np.mean(covs)),
            "lift": mean_lift,
            "save": nmax - float(np.mean(sizes)),
        })
        series[name] = mask_at
    rows.sort(key=lambda r: -r["lift"])
    return rows, series


def pairwise_and(rows, series, next_bits, nmax, k, t0, t1, limit_atomic=12):
    FULL = full_bits(nmax)
    top = [r["name"] for r in rows[:limit_atomic]]
    extra = []
    extra_series = {}
    for i, a in enumerate(top):
        for b in top[i + 1:]:
            name = f"AND({a}|{b})"
            lifts, sizes, covs = [], [], []
            n_fire = 0
            mask_at = np.zeros(t1, dtype=np.int64)
            sa, sb = series[a], series[b]
            for t in range(t0, t1):
                m = int(sa[t] & sb[t])
                if popcount(m) < POOL:
                    m = FULL
                mask_at[t] = m
                sz = popcount(m)
                cov = coverage(m, int(next_bits[t]), k)
                if m != FULL and sz <= nmax - MIN_SAVE:
                    n_fire += 1
                    lifts.append(cov / max(sz / nmax, 1e-9))
                    sizes.append(sz)
                    covs.append(cov)
            if n_fire < MIN_FIRE:
                continue
            extra.append({
                "name": name,
                "n_fire": n_fire,
                "fire_pct": 100.0 * n_fire / (t1 - t0),
                "mean_size": float(np.mean(sizes)),
                "mean_cov": float(np.mean(covs)),
                "lift": float(np.mean(lifts)),
                "save": nmax - float(np.mean(sizes)),
            })
            extra_series[name] = mask_at
    extra.sort(key=lambda r: -r["lift"])
    return extra, extra_series


def permutation_p(mask_at, next_bits, nmax, k, t0, t1, obs_lift, nperm=NPERM):
    """Null: next draws shuffled; masks stay tied to calendar time."""
    FULL = full_bits(nmax)
    idx = np.arange(t0, t1)
    nxt = next_bits[t0:t1].copy()
    cnt = 0
    for _ in range(nperm):
        RNG.shuffle(nxt)
        lifts = []
        for i, t in enumerate(idx):
            m = int(mask_at[t])
            sz = popcount(m)
            if m == FULL or sz > nmax - MIN_SAVE:
                continue
            cov = coverage(m, int(nxt[i]), k)
            lifts.append(cov / max(sz / nmax, 1e-9))
        if lifts and float(np.mean(lifts)) >= obs_lift:
            cnt += 1
    return (cnt + 1) / (nperm + 1)


def freq_arr(hist, max_num):
    n = hist.shape[0]
    if n == 0:
        return np.ones(max_num, dtype=np.float64)
    w = np.exp(np.linspace(-2.0, 0.0, n))
    s = np.zeros(max_num + 1, dtype=np.float64)
    rows = hist if hist.ndim == 2 else hist.reshape(-1, 1)
    for wi, row in zip(w, rows):
        for v in np.atleast_1d(row):
            vi = int(v)
            if 1 <= vi <= max_num:
                s[vi] += wi
    return s[1:]


def confirm_urna1(draws, name, fn, start):
    test = draws[start:]
    n_test = len(test)
    hs_m, hs_f = [], []
    pos = 0
    last_m = {}
    last_f = {}
    while pos < n_test:
        end = min(pos + BLOCK, n_test)
        t = start + pos
        hist = draws[:t]
        f = freq_arr(hist, N1)
        last_f = {i + 1: float(f[i]) for i in range(N1)}
        mbits = int(fn(t)) if fn is not None else full_bits(N1)
        fm = f.copy()
        for i in range(N1):
            if (mbits >> i) & 1 == 0:
                fm[i] = 0.0
        last_m = {i + 1: float(fm[i]) for i in range(N1)}
        top_m = set(rank_by_score(last_m, POOL))
        top_f = set(rank_by_score(last_f, POOL))
        for j in range(pos, end):
            actual = set(int(x) for x in test[j])
            hs_m.append(len(top_m & actual))
            hs_f.append(len(top_f & actual))
        pos = end
    hs_m = np.array(hs_m); hs_f = np.array(hs_f)
    a = np.array([last_m[i] for i in range(1, N1 + 1)])
    b = np.array([last_f[i] for i in range(1, N1 + 1)])
    ra = np.argsort(np.argsort(a)).astype(np.float64)
    rb = np.argsort(np.argsort(b)).astype(np.float64)
    ra -= ra.mean(); rb -= rb.mean()
    den = float(np.sqrt((ra * ra).sum() * (rb * rb).sum()))
    sp = 0.0 if den < 1e-12 else float((ra * rb).sum() / den)
    pool = rank_by_score(last_m, POOL)
    n3m = int((hs_m >= 3).sum()); n3f = int((hs_f >= 3).sum())
    only_m = int(((hs_m >= 3) & (hs_f < 3)).sum())
    only_f = int(((hs_m < 3) & (hs_f >= 3)).sum())
    return {
        "name": name,
        "n": len(hs_m),
        "r3": n3m / len(hs_m),
        "r3_freq": n3f / len(hs_f),
        "r4": float((hs_m >= 4).mean()),
        "d_n3": n3m - n3f,
        "only_m": only_m,
        "only_f": only_f,
        "avg": float(hs_m.mean()),
        "spearman": abs(sp),
        "block": is_consecutive_block(pool, min_size=6),
        "nuniq": len({round(v, 8) for v in last_m.values()}),
    }


def confirm_urna2(jokers, name, fn, start):
    test = jokers[start:]
    n_test = len(test)
    hit1_m = hit1_f = hit3_m = hit3_f = 0
    pos = 0
    while pos < n_test:
        end = min(pos + BLOCK, n_test)
        t = start + pos
        hist = jokers[:t]
        f = freq_arr(hist, N2)
        fd = {i + 1: float(f[i]) for i in range(N2)}
        mbits = int(fn(t))
        fm = f.copy()
        for i in range(N2):
            if (mbits >> i) & 1 == 0:
                fm[i] = 0.0
        md = {i + 1: float(fm[i]) for i in range(N2)}
        top1_m = rank_by_score(md, 1)[0]
        top1_f = rank_by_score(fd, 1)[0]
        top3_m = set(rank_by_score(md, 3))
        top3_f = set(rank_by_score(fd, 3))
        for j in range(pos, end):
            actual = int(test[j])
            hit1_m += int(top1_m == actual)
            hit1_f += int(top1_f == actual)
            hit3_m += int(actual in top3_m)
            hit3_f += int(actual in top3_f)
        pos = end
    n = n_test
    return {
        "name": name,
        "n": n,
        "hit1": hit1_m / n,
        "hit1_freq": hit1_f / n,
        "hit3": hit3_m / n,
        "hit3_freq": hit3_f / n,
        "d1": hit1_m - hit1_f,
        "d3": hit3_m - hit3_f,
    }


def print_disc(title, rows, n_masks, pvals=None):
    print(f"\n{title}  (măști cu fire≥{MIN_FIRE} și save≥{MIN_SAVE}: {len(rows)} / {n_masks})")
    print(f"  {'lift':>6} {'fire%':>7} {'|M|':>5} {'cov':>5} {'p_perm':>8}  name")
    for r in rows[:15]:
        p = f"{pvals[r['name']]:.4f}" if pvals and r["name"] in pvals else "  n/a "
        print(
            f"  {r['lift']:6.3f} {r['fire_pct']:6.1f}% {r['mean_size']:5.1f} "
            f"{r['mean_cov']:5.3f} {p:>8}  {r['name']}"
        )


def main():
    t_wall = time.perf_counter()
    u1, u2 = load()
    n = len(u1)
    split = int(n * SPLIT)
    print(f"Joker n={n} split_disc={split} confirm={n - split} "
          f"urna1={N1}/{K1} urna2=1/{N2} perm={NPERM}")

    next1 = np.array([bits_from_nums(row, N1) for row in u1], dtype=np.int64)
    next2 = np.array([1 << (int(j) - 1) for j in u2], dtype=np.int64)
    gaps1 = gaps_matrix(u1, N1)
    gaps2 = gaps_matrix(u2, N2)

    # ---- Urna 1 discovery ----
    b1 = catalog_urna1(u1, gaps1)
    print(f"\n[U1] catalog atomic = {len(b1)} măști")
    # t0 skips short windows
    t0, t1 = 8, split
    rows1, ser1 = eval_discovery(b1, next1, N1, K1, t0, t1)
    extra1, extra_s1 = pairwise_and(rows1, ser1, next1, N1, K1, t0, t1)
    print(f"[U1] AND perechi reținute = {len(extra1)}")
    all1 = rows1 + extra1
    all1.sort(key=lambda r: -r["lift"])
    ser1.update(extra_s1)
    n_tested = len(b1) + len(extra1)

    # permute top 25
    pvals1 = {}
    for r in all1[:25]:
        pvals1[r["name"]] = permutation_p(
            ser1[r["name"]], next1, N1, K1, t0, t1, r["lift"]
        )
    print_disc("[U1] DESCOPERIRE (primele 70%)", all1, n_tested, pvals1)
    alpha = 0.05 / max(n_tested, 1)
    surv1 = [r for r in all1[:25] if pvals1.get(r["name"], 1) < alpha]
    print(f"[U1] Bonferroni α={alpha:.5f} — supraviețuitori: {len(surv1)}")
    for r in surv1:
        print(f"    {r['name']} lift={r['lift']:.3f} p={pvals1[r['name']]:.4f}")

    # confirm: top by lift + any survivors
    names_c = []
    for r in all1[:TOP_CONFIRM]:
        names_c.append(r["name"])
    for r in surv1:
        if r["name"] not in names_c:
            names_c.append(r["name"])
    fn_by = dict(b1)
    print(f"\n[U1] CONFIRMARE WF ultimele 30%  pool={POOL}  vs frequency")
    print(f"  {'method':40s} {'r3':>7} {'Δn3':>5} {'r3freq':>7} {'|ρ|':>5} blk")
    confirms = []
    # frequency baseline via a no-op mask
    def full_fn(t, _F=full_bits(N1)):
        return _F
    base = confirm_urna1(u1, "frequency", full_fn, split)
    print(
        f"  {'frequency':40s} {100*base['r3']:6.2f}% {0:5d} "
        f"{100*base['r3_freq']:6.2f}% {base['spearman']:5.2f} "
        f"{'BLK' if base['block'] else 'ok'}"
    )
    for name in names_c:
        fn = fn_by.get(name)
        if fn is None and name.startswith("AND("):
            a, b = name[4:-1].split("|")
            fa, fb = fn_by[a], fn_by[b]
            def and_fn(t, fa=fa, fb=fb):
                m = int(fa(t) & fb(t))
                return m if popcount(m) >= POOL else full_bits(N1)
            fn = and_fn
        rec = confirm_urna1(u1, name, fn, split)
        confirms.append(rec)
        flag = " *" if rec["d_n3"] > 0 else ""
        print(
            f"  {name:40s} {100*rec['r3']:6.2f}% {rec['d_n3']:+5d} "
            f"{100*rec['r3_freq']:6.2f}% {rec['spearman']:5.2f} "
            f"{'BLK' if rec['block'] else 'ok'}{flag}"
        )

    ok_u1 = [
        r for r in confirms
        if r["d_n3"] > 0 and r["spearman"] < 0.95 and not r["block"]
    ]
    print(f"[U1] trec poarta scoring (Δn3>0, |ρ|<0.95, nu bloc): {len(ok_u1)}")
    for r in ok_u1:
        print(f"    {r['name']} Δn3={r['d_n3']:+d} |ρ|={r['spearman']:.3f} "
              f"McNemar {r['only_m']} vs {r['only_f']}")

    # ---- Urna 2 ----
    b2 = catalog_urna2(u2, gaps2)
    print(f"\n[U2] catalog atomic = {len(b2)} măști")
    rows2, ser2 = eval_discovery(b2, next2, N2, 1, t0, t1)
    n_tested2 = len(b2)
    pvals2 = {}
    for r in rows2[:20]:
        pvals2[r["name"]] = permutation_p(
            ser2[r["name"]], next2, N2, 1, t0, t1, r["lift"]
        )
    print_disc("[U2] DESCOPERIRE (primele 70%)", rows2, n_tested2, pvals2)
    alpha2 = 0.05 / max(n_tested2, 1)
    surv2 = [r for r in rows2[:20] if pvals2.get(r["name"], 1) < alpha2]
    print(f"[U2] Bonferroni α={alpha2:.5f} — supraviețuitori: {len(surv2)}")

    fn2 = dict(b2)
    print(f"\n[U2] CONFIRMARE hit@1 / hit@3 vs frequency  (p_rand@1={1/N2:.3f})")
    print(f"  {'method':32s} {'h1':>7} {'Δ1':>4} {'h1freq':>7} {'h3':>7} {'Δ3':>4}")
    names2 = [r["name"] for r in rows2[:12]]
    for r in surv2:
        if r["name"] not in names2:
            names2.append(r["name"])
    c2 = []
    for name in names2:
        rec = confirm_urna2(u2, name, fn2[name], split)
        c2.append(rec)
        print(
            f"  {name:32s} {100*rec['hit1']:6.2f}% {rec['d1']:+4d} "
            f"{100*rec['hit1_freq']:6.2f}% {100*rec['hit3']:6.2f}% {rec['d3']:+4d}"
        )
    # frequency baseline urna2
    rec_f = confirm_urna2(u2, "frequency", lambda t: full_bits(N2), split)
    print(
        f"  {'frequency':32s} {100*rec_f['hit1']:6.2f}% {0:4d} "
        f"{100*rec_f['hit1_freq']:6.2f}% {100*rec_f['hit3']:6.2f}% {0:4d}"
    )
    ok_u2 = [r for r in c2 if r["d1"] > 0]
    print(f"[U2] Δ hit@1 > 0 vs frequency: {len(ok_u2)}")

    print("\n" + "=" * 72)
    print("DECIZIE SCORER")
    print("=" * 72)
    if surv1 and ok_u1:
        print("U1: există mască cu p Bonferroni + scoring pe confirmare. CANDIDAT.")
    else:
        print(
            "U1: NU. Lifturile de descoperire nu trec permutarea ajustată și/sau "
            "pe ultimele 30% masca e clonă de frequency / pierde la 3+."
        )
    if surv2 and ok_u2:
        print("U2: există mască cu p Bonferroni + hit@1 peste frequency. CANDIDAT.")
    else:
        print(
            "U2: NU. Reducerea 1–20 nu bate frequency la hit@1 pe confirmare "
            "după corecție de teste multiple."
        )
    print(f"durată {time.perf_counter() - t_wall:.1f}s")


def smoke() -> None:
    """Sanity: catalogul rulează, măștile întorc biți în 1..N. Fără WF."""
    u1, u2 = load()
    assert len(u1) >= 100 and len(u2) == len(u1)
    g1 = gaps_matrix(u1, N1)
    g2 = gaps_matrix(u2, N2)
    b1 = catalog_urna1(u1, g1)
    b2 = catalog_urna2(u2, g2)
    assert len(b1) >= 150, len(b1)
    assert len(b2) >= 50, len(b2)
    t = 40
    for name, fn in b1[:8] + b2[:8]:
        bits = int(fn(t))
        nmax = N1 if not name.startswith("u2_") else N2
        sz = popcount(bits)
        assert 1 <= sz <= nmax, (name, sz, nmax)
    print(f"smoke ok  u1_catalog={len(b1)} u2_catalog={len(b2)} n={len(u1)}")


if __name__ == "__main__":
    if "--smoke" in sys.argv:
        smoke()
    else:
        main()
