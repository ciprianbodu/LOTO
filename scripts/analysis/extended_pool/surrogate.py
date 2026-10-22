"""Fast pure-Python annealing on the k<=5 part of the exact 4+ numerator:

    S = w4*D4 + w5*F5 + lam3*w3*D3 + lam5*w5*D5

D_t = distinct t-subsets of the pool lying on at least one variant, F5 = 5-subsets
of the pool containing a covered 4-set, w_k = C(max_num - v, draw_n - k).
For Joker (draw 5) w4*D4 + w5*F5 IS the exact 4+ numerator; for 6/49 and 5/40
only the k=6 term (weight 1) is missing. The exact objective (anneal.Inc)
polishes afterwards and every reported number comes from lib_exact.
"""
from __future__ import annotations

import math
import time
from math import comb


def surrogate_sa(v, pick, draw_n, max_num, masks, seconds, rng, lam3=0.01, lam5=0.01,
                 T0f=2.0, T1f=0.05, log=None, w4mult=1.0, hard_pack=False):
    out = max_num - v
    w = {k: (comb(out, draw_n - k) if 0 <= draw_n - k <= out else 0) for k in range(7)}
    W4, W5 = float(w[4]) * w4mult, float(w[5])  # w4mult > 1: favour 4-packings
    W3 = lam3 * w[3]
    W5d = lam5 * w[5] if pick == 6 else 0.0  # D5 is constant (= b) for 5-number tickets
    bit = [1 << p for p in range(v)]
    size = 1 << v
    mult = [0] * size  # blocks containing T, for |T| = 3, 4, 5
    c5 = [0] * size    # covered 4-subsets of a 5-set
    bmask = [int(m) for m in masks]
    blocks = [[p for p in range(v) if m >> p & 1] for m in bmask]
    bset = set(bmask)
    b = len(blocks)

    def subsets_by_size(pos):
        sz = [[0]] + [[] for _ in range(len(pos))]
        for p in pos:
            bp = bit[p]
            for k in range(len(pos) - 1, -1, -1):
                if sz[k]:
                    sz[k + 1].extend([s | bp for s in sz[k]])
        return sz

    for B in blocks:
        sz = subsets_by_size(B)
        for t in (3, 4, 5):
            if t <= pick and t < pick or (t == 5 and pick == 6) or (t <= 4):
                for m in sz[t]:
                    mult[m] += 1
    D3 = D4 = D5 = 0
    for B in blocks:
        pass
    # distinct counts
    seen3, seen4, seen5 = set(), set(), set()
    for B in blocks:
        sz = subsets_by_size(B)
        seen3.update(sz[3])
        seen4.update(sz[4])
        if pick == 6:
            seen5.update(sz[5])
    D3, D4, D5 = len(seen3), len(seen4), len(seen5)
    sup5 = [None] * size  # 5-supersets of every 4-set
    import itertools as _it
    for C in _it.combinations(range(v), 4):
        T = bit[C[0]] | bit[C[1]] | bit[C[2]] | bit[C[3]]
        sup5[T] = tuple(T | bp for bp in bit if not T & bp)
    for T in seen4:
        for k in sup5[T]:
            c5[k] += 1
    F5 = sum(1 for S in range(size) if c5[S] > 0) if v <= 20 else 0
    S_val = W4 * D4 + W5 * F5 + W3 * D3 + W5d * D5
    best = (S_val, list(bmask), {"D3": D3, "D4": D4, "D5": D5, "F5": F5})
    T0, T1 = T0f * W4, T1f * W4
    start = time.time()
    moves = acc = 0
    rand, randrange, exp = rng.random, rng.randrange, math.exp
    last_log = start
    while True:
        el = time.time() - start
        if el >= seconds:
            break
        T = T0 * (T1 / T0) ** (el / seconds)
        for _ in range(1000):
            moves += 1
            bi = randrange(b)
            B = blocks[bi]
            i = randrange(pick)
            x = B[i]
            bm = bmask[bi]
            y = randrange(v)
            while bm >> y & 1:
                y = randrange(v)
            nm = (bm ^ bit[x]) | bit[y]
            if nm in bset:
                continue
            xb, yb = bit[x], bit[y]
            O = B[:i] + B[i + 1:]
            sz = subsets_by_size(O)
            lost4 = [s | xb for s in sz[3]]
            gain4 = [s | yb for s in sz[3]]
            unc = [T_ for T_ in lost4 if mult[T_] == 1]
            cov = [T_ for T_ in gain4 if mult[T_] == 0]
            if hard_pack and len(cov) != len(gain4):
                continue  # would put a 4-set on two variants: stay a perfect 4-packing
            dD4 = len(cov) - len(unc)
            lost3 = [s | xb for s in sz[2]]
            gain3 = [s | yb for s in sz[2]]
            dD3 = sum(1 for T_ in gain3 if mult[T_] == 0) - sum(1 for T_ in lost3 if mult[T_] == 1)
            if pick == 6:
                lost5 = [s | xb for s in sz[4]]
                gain5 = [s | yb for s in sz[4]]
                dD5 = sum(1 for T_ in gain5 if mult[T_] == 0) - sum(1 for T_ in lost5 if mult[T_] == 1)
            else:
                lost5 = gain5 = ()
                dD5 = 0
            dA = W4 * dD4 + W3 * dD3 + W5d * dD5
            thr = T * math.log(1.0 - rand())  # accept iff d >= thr (Metropolis)
            if cov:
                gain_ub = sum(1 for T_ in cov for k in sup5[T_] if c5[k] == 0)
            else:
                gain_ub = 0
            if dA + W5 * gain_ub < thr:
                continue  # rejected whatever the 5-set term is
            dF5 = 0
            dc = None
            if unc or cov:
                dc = {}
                get = dc.get
                for T_ in unc:
                    for k in sup5[T_]:
                        dc[k] = get(k, 0) - 1
                for T_ in cov:
                    for k in sup5[T_]:
                        dc[k] = get(k, 0) + 1
                for k, dl in dc.items():
                    if dl:
                        bef = c5[k]
                        aft = bef + dl
                        if bef == 0:
                            if aft > 0:
                                dF5 += 1
                        elif aft == 0:
                            dF5 -= 1
            d = dA + W5 * dF5
            if d >= thr:
                acc += 1
                for T_ in lost4:
                    mult[T_] -= 1
                for T_ in gain4:
                    mult[T_] += 1
                for T_ in lost3:
                    mult[T_] -= 1
                for T_ in gain3:
                    mult[T_] += 1
                for T_ in lost5:
                    mult[T_] -= 1
                for T_ in gain5:
                    mult[T_] += 1
                if dc:
                    for k, dl in dc.items():
                        c5[k] += dl
                D3 += dD3
                D4 += dD4
                D5 += dD5
                F5 += dF5
                S_val += d
                bset.discard(bm)
                bset.add(nm)
                bmask[bi] = nm
                nb = O + [y]
                nb.sort()
                blocks[bi] = nb
                if S_val > best[0] + 1e-9:
                    best = (S_val, list(bmask), {"D3": D3, "D4": D4, "D5": D5, "F5": F5})
        if log and time.time() - last_log > 30:
            last_log = time.time()
            log(f"  [surr] t={el:6.1f}s T/W4={T/W4:.3f} D4={D4} F5={F5} D3={D3} "
                f"best={best[0]:.0f} {best[2]} moves={moves} acc={acc}")
    return best, {"moves": moves, "accepted": acc, "T0/W4": T0f, "T1/W4": T1f}
