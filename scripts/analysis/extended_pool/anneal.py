"""Greedy 4-cover (truncated / extended to the budget) + simulated annealing on
the EXACT objective  J = N4 + lam3*N3 + lam5*N5  (N_t = number of draws, out of
C(max_num, draw_n), with >= t hits on at least one variant), pool positions
0..v-1, v <= 20.  Incremental evaluation: for every threshold t, mult[T] =
blocks containing the t-set T, c[S] = covered t-subsets inside S (|S| <= draw_n);
S counts iff c[S] > 0, weight C(max_num - v, draw_n - |S|).

Usage: anneal.py GAME BUDGET V SECONDS SEED OUT.json [lam3] [lam5] [init.json]
"""
from __future__ import annotations

import itertools
import json
import math
import random
import sys
import time
from math import comb
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lib_exact as L  # noqa: E402


class Inc:
    def __init__(self, v, pick, draw_n, max_num, masks, lam):
        self.v, self.pick, self.d, self.N = v, pick, draw_n, max_num
        size = 1 << v
        self.pc = np.bitwise_count(np.arange(size, dtype=np.uint32)).astype(np.int64)
        outside = max_num - v
        self.wk = np.array(
            [comb(outside, draw_n - k) if 0 <= draw_n - k <= outside else 0 for k in range(v + 1)],
            dtype=np.int64,
        )
        self.lam = {t: float(x) for t, x in lam.items() if x and t <= min(pick, draw_n)}
        # threshold t == pick at draw_n == pick is constant (= distinct tickets)
        if pick == draw_n and pick in self.lam:
            del self.lam[pick]
        self.ts = sorted(self.lam)
        self.idx, self.sup, self.supw, self.mult, self.c, self.Nt = {}, {}, {}, {}, {}, {}
        self.osub = {}
        for t in self.ts:
            tsets = list(itertools.combinations(range(v), t))
            tm = np.array([sum(1 << p for p in T) for T in tsets], dtype=np.int64)
            idx = np.full(size, -1, dtype=np.int32)
            idx[tm] = np.arange(len(tsets), dtype=np.int32)
            Lw = sum(comb(v - t, j) for j in range(0, draw_n - t + 1))
            sup = np.empty((len(tsets), Lw), dtype=np.int64)
            pats = [None] + [
                np.array(list(itertools.combinations(range(v - t), j)), dtype=np.int64).reshape(-1, j)
                for j in range(1, draw_n - t + 1)
            ]
            allpos = np.arange(v)
            for i, T in enumerate(tsets):
                comp = np.setdiff1d(allpos, T)
                bits = (np.int64(1) << comp)
                col = 0
                for j, pat in enumerate(pats):
                    if j == 0:
                        sup[i, col] = tm[i]
                        col += 1
                    else:
                        vals = tm[i] | bits[pat].sum(axis=1)
                        sup[i, col:col + len(vals)] = vals
                        col += len(vals)
            self.idx[t] = idx
            self.sup[t] = sup
            self.supw[t] = self.wk[self.pc[sup]]
            # (t-1)-subsets of the pick-1 "other" positions of a block
            self.osub[t] = list(itertools.combinations(range(pick - 1), t - 1))
        self.set_blocks(masks)

    def set_blocks(self, masks):
        size = 1 << self.v
        self.blocks = [int(m) for m in masks]
        self.bset = set(self.blocks)
        for t in self.ts:
            mult = np.zeros(size, dtype=np.int16)
            for B in self.blocks:
                pos = [p for p in range(self.v) if B >> p & 1]
                for T in itertools.combinations(pos, t):
                    mult[sum(1 << p for p in T)] += 1
            c = np.zeros(size, dtype=np.int16)
            covered = np.nonzero(mult)[0]
            rows = self.idx[t][covered]
            np.add.at(c, self.sup[t][rows].ravel(), 1)
            self.mult[t] = mult
            self.c[t] = c
            self.Nt[t] = int(self.wk[self.pc[np.nonzero(c)[0]]].sum())

    def J(self):
        return sum(self.lam[t] * self.Nt[t] for t in self.ts)

    def propose(self, bi, x, y):
        """Delta of replacing position x by y in block bi (read-only)."""
        B = self.blocks[bi]
        B2 = (B & ~(1 << x)) | (1 << y)
        if B2 in self.bset:
            return None
        others = [p for p in range(self.v) if (B >> p & 1) and p != x]
        xb, yb = 1 << x, 1 << y
        dJ = 0.0
        ch = {}
        for t in self.ts:
            om = np.array([sum(1 << others[i] for i in s) for s in self.osub[t]], dtype=np.int64)
            lost = om | xb
            gain = om | yb
            mult = self.mult[t]
            lz = lost[mult[lost] == 1]
            gn = gain[mult[gain] == 0]
            if len(lz) == 0 and len(gn) == 0:
                ch[t] = (lost, gain, None, None, 0)
                continue
            idx = self.idx[t]
            sup = self.sup[t]
            S = np.concatenate([sup[idx[lz]].ravel(), sup[idx[gn]].ravel()])
            dl = np.concatenate([
                np.full(len(lz) * sup.shape[1], -1, dtype=np.int64),
                np.full(len(gn) * sup.shape[1], 1, dtype=np.int64),
            ])
            uniq, inv = np.unique(S, return_inverse=True)
            dd = np.bincount(inv, weights=dl, minlength=len(uniq)).astype(np.int64)
            before = self.c[t][uniq].astype(np.int64)
            after = before + dd
            dn = int((self.wk[self.pc[uniq]] * ((after > 0).astype(np.int64) - (before > 0))).sum())
            ch[t] = (lost, gain, uniq, after, dn)
            dJ += self.lam[t] * dn
        return dJ, B2, ch

    def apply(self, bi, B2, ch):
        B = self.blocks[bi]
        self.bset.discard(B)
        self.bset.add(B2)
        self.blocks[bi] = B2
        for t in self.ts:
            lost, gain, uniq, after, dn = ch[t]
            self.mult[t][lost] -= 1
            self.mult[t][gain] += 1
            if uniq is not None:
                self.c[t][uniq] = after.astype(np.int16)
                self.Nt[t] += dn


def all_blocks(v, pick):
    return np.array(
        [sum(1 << p for p in c) for c in itertools.combinations(range(v), pick)], dtype=np.int64
    )


def greedy_init(v, pick, budget, rng):
    """Greedy 4-cover on positions 0..v-1: each step the block covering the most
    uncovered 4-sets (ties: most uncovered 3-sets, then random). Truncated at
    the budget; if the 4-cover completes first, extended greedily on 5-sets
    (then 3-sets) so the budget is used."""
    cands = all_blocks(v, pick)
    ncand = len(cands)
    budget = min(budget, ncand)
    sub = {}
    for t in (3, 4, 5):
        if t > pick:
            continue
        tsets = list(itertools.combinations(range(v), t))
        tm = {sum(1 << p for p in T): i for i, T in enumerate(tsets)}
        rows = []
        for c in itertools.combinations(range(v), pick):
            rows.append([tm[sum(1 << p for p in T)] for T in itertools.combinations(c, t)])
        sub[t] = (np.array(rows, dtype=np.int32), np.zeros(len(tsets), dtype=bool))
    chosen = np.zeros(ncand, dtype=bool)
    out = []
    noise = np.array([rng.random() for _ in range(ncand)])
    while len(out) < budget:
        s4 = (~sub[4][1])[sub[4][0]].sum(axis=1).astype(np.float64)
        s3 = (~sub[3][1])[sub[3][0]].sum(axis=1).astype(np.float64)
        s5 = (~sub[5][1])[sub[5][0]].sum(axis=1).astype(np.float64) if 5 in sub else 0.0
        if s4.max() > 0:
            score = s4 * 1e6 + s3 * 1e3 + s5 + noise
        else:
            score = (s5 * 1e6 if 5 in sub else 0.0) + s3 * 1e3 + noise
        score[chosen] = -1
        j = int(np.argmax(score))
        chosen[j] = True
        out.append(int(cands[j]))
        for t, (rows, cov) in sub.items():
            cov[rows[j]] = True
        noise = np.array([rng.random() for _ in range(ncand)])
    return out


def anneal(inc: Inc, seconds, rng, log_every=20.0):
    v, b = inc.v, len(inc.blocks)
    # calibrate temperature on random moves
    negs = []
    for _ in range(400):
        bi = rng.randrange(b)
        B = inc.blocks[bi]
        x = rng.choice([p for p in range(v) if B >> p & 1])
        y = rng.choice([p for p in range(v) if not B >> p & 1])
        r = inc.propose(bi, x, y)
        if r and r[0] < 0:
            negs.append(-r[0])
    scale = float(np.median(negs)) if negs else 1.0
    T0, T1 = 0.5 * scale, 0.002 * scale
    best_J = inc.J()
    best = (list(inc.blocks), dict(inc.Nt))
    start = time.time()
    moves = acc = 0
    last = start
    while True:
        el = time.time() - start
        if el >= seconds:
            break
        tau = el / seconds
        T = T0 * (T1 / T0) ** tau
        for _ in range(200):
            bi = rng.randrange(b)
            B = inc.blocks[bi]
            x = rng.choice([p for p in range(v) if B >> p & 1])
            y = rng.choice([p for p in range(v) if not B >> p & 1])
            r = inc.propose(bi, x, y)
            moves += 1
            if r is None:
                continue
            dJ, B2, ch = r
            if dJ >= 0 or rng.random() < math.exp(dJ / T):
                inc.apply(bi, B2, ch)
                acc += 1
                J = inc.J()
                if J > best_J + 1e-9:
                    best_J = J
                    best = (list(inc.blocks), dict(inc.Nt))
        if time.time() - last > log_every:
            last = time.time()
            print(f"  t={el:6.1f}s T={T:9.2f} J={inc.J():.1f} best={best_J:.1f} "
                  f"N={inc.Nt} moves={moves} acc={acc}", flush=True)
    return best, {"moves": moves, "accepted": acc, "T0": T0, "T1": T1, "scale": scale}


def descend(inc: Inc, rng, max_rounds=3):
    """Exhaustive first-improvement single-swap descent (zero temperature)."""
    v, b = inc.v, len(inc.blocks)
    improved = True
    rounds = 0
    while improved and rounds < max_rounds:
        improved = False
        rounds += 1
        order = list(range(b))
        rng.shuffle(order)
        for bi in order:
            B = inc.blocks[bi]
            for x in [p for p in range(v) if B >> p & 1]:
                for y in [p for p in range(v) if not B >> p & 1]:
                    if not (inc.blocks[bi] >> x & 1) or (inc.blocks[bi] >> y & 1):
                        continue
                    r = inc.propose(bi, x, y)
                    if r and r[0] > 1e-9:
                        inc.apply(bi, r[1], r[2])
                        improved = True
    return rounds


def main():
    game, budget, v, seconds, seed, out = sys.argv[1:7]
    lam3 = float(sys.argv[7]) if len(sys.argv) > 7 else 0.01
    lam5 = float(sys.argv[8]) if len(sys.argv) > 8 else 0.01
    init = sys.argv[9] if len(sys.argv) > 9 else None
    budget, v, seconds, seed = int(budget), int(v), float(seconds), int(seed)
    pick, draw_n, max_num = L.GAMES[game]
    rng = random.Random(seed)
    t0 = time.time()
    if init:
        blocks = json.loads(Path(init).read_text())["blocks"]
        masks = L.masks_of(blocks)
        src = f"init {init}"
    else:
        masks = greedy_init(v, pick, budget, rng)
        src = "greedy"
    g_exact = L.exact(v, [[p + 1 for p in range(v) if m >> p & 1] for m in masks], game)
    print(f"{game} b={budget} v={v}: {src} P={g_exact['P']} cov4={g_exact['covered4']} "
          f"({time.time()-t0:.1f}s)", flush=True)
    inc = Inc(v, pick, draw_n, max_num, masks, {4: 1.0, 3: lam3, 5: lam5})
    print(f"  inc init N={inc.Nt} ({time.time()-t0:.1f}s)", flush=True)
    (best_blocks, best_N), stats = anneal(inc, seconds, rng)
    inc.set_blocks(best_blocks)
    rounds = descend(inc, rng)
    blocks = [[p + 1 for p in range(v) if m >> p & 1] for m in inc.blocks]
    ex = L.exact(v, blocks, game)
    for t in inc.ts:  # incremental state must equal the exact DP
        assert inc.Nt[t] == ex["num"][t] or t not in ex["num"], (t, inc.Nt[t], ex["num"].get(t))
    res = {
        "game": game, "budget": budget, "v": v, "seed": seed, "seconds": seconds,
        "lam3": lam3, "lam5": lam5, "init": src,
        "greedy_P": g_exact["P"], "greedy_covered4": g_exact["covered4"],
        "P": ex["P"], "num": ex["num"], "covered4": ex["covered4"], "frac4": ex["frac4"],
        "covered3": ex["covered3"], "frac3": ex["frac3"],
        "stats": stats, "descent_rounds": rounds, "wall": round(time.time() - t0, 1),
        "blocks": sorted(blocks),
    }
    Path(out).write_text(json.dumps(res, indent=0), "utf-8")
    print(f"DONE {game} b={budget} v={v} P4={ex['P'][4]*100:.6f}% P3={ex['P'][3]*100:.6f}% "
          f"cov4={ex['covered4']}/{comb(v,4)} moves={stats['moves']} wall={res['wall']}s", flush=True)


if __name__ == "__main__":
    main()
