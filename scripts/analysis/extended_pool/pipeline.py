"""Seeded two-phase pipeline: greedy 4-cover from a seed (truncated / extended
to the budget) -> surrogate SA (pure Python, k<=5 part of the exact 4+
numerator) -> exact SA polish (anneal.Inc, numpy incremental on the exact
objective N4 + lam3*N3 + lam5*N5) -> exhaustive single-swap descent.
Every number written comes from lib_exact (2^v DP); the incremental state is
asserted equal to it.

Usage: pipeline.py GAME BUDGET V SECONDS SEED OUT.json [SEEDKIND] [lam3] [lam5]
SEEDKIND: greedy | lr:<game>:<code> | path to a result json (positions 1..v)
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
from anneal import Inc, all_blocks, descend  # noqa: E402
from surrogate import surrogate_sa  # noqa: E402


def greedy_init(v, pick, budget, rng, initial=()):
    """Greedy 4-cover on positions 0..v-1: each step the block covering the most
    uncovered 4-sets (ties: most uncovered 3-sets, then 5-sets, then random).
    Starts from `initial` (masks); truncated at the budget (dropping the blocks
    that cover the fewest 4-sets alone); if the 4-cover completes first it is
    extended greedily on 5-sets (then 3-sets) so the budget is used."""
    cands = all_blocks(v, pick)
    ncand = len(cands)
    budget = min(budget, ncand)
    pos_of = {int(m): i for i, m in enumerate(cands)}
    sub = {}
    for t in (3, 4, 5):
        if t > pick:
            continue
        cache = HERE / "cache" / f"rows_{v}_{pick}_{t}.npy"
        ntsets = comb(v, t)
        try:
            rows = np.load(cache)
        except OSError:
            tsets = list(itertools.combinations(range(v), t))
            tm = {sum(1 << p for p in T): i for i, T in enumerate(tsets)}
            rows = []
            for c in itertools.combinations(range(v), pick):
                rows.append([tm[sum(1 << p for p in T)] for T in itertools.combinations(c, t)])
            rows = np.array(rows, dtype=np.int32)
            try:
                np.save(cache, rows)
            except OSError:
                pass
        sub[t] = (rows, np.zeros(ntsets, dtype=np.int32))
    nprng = np.random.default_rng(rng.randrange(2**32))
    chosen = np.zeros(ncand, dtype=bool)
    out = []
    for m in initial:
        j = pos_of[int(m)]
        if not chosen[j]:
            chosen[j] = True
            out.append(int(m))
            for t, (rows, cov) in sub.items():
                np.add.at(cov, rows[j], 1)
    while len(out) > budget:
        idxs = [pos_of[m] for m in out]
        rows4, cov4 = sub[4]
        alone = (cov4[rows4[idxs]] == 1).sum(axis=1) + np.array([rng.random() for _ in idxs])
        k = int(np.argmin(alone))
        j = idxs[k]
        chosen[j] = False
        for t, (rows, cov) in sub.items():
            np.subtract.at(cov, rows[j], 1)
        out.pop(k)
    while len(out) < budget:
        noise = nprng.random(ncand)
        s4 = (sub[4][1][sub[4][0]] == 0).sum(axis=1).astype(np.float64)
        s3 = (sub[3][1][sub[3][0]] == 0).sum(axis=1).astype(np.float64)
        s5 = (sub[5][1][sub[5][0]] == 0).sum(axis=1).astype(np.float64) if 5 in sub else 0.0
        if s4[~chosen].max() > 0:
            score = s4 * 1e6 + s3 * 1e3 + s5 + noise
        else:
            score = (s5 * 1e6 if 5 in sub else 0.0) + s3 * 1e3 + noise
        score[chosen] = -1
        j = int(np.argmax(score))
        chosen[j] = True
        out.append(int(cands[j]))
        for t, (rows, cov) in sub.items():
            np.add.at(cov, rows[j], 1)
    return out


def exact_anneal(inc: Inc, seconds, rng, T0, T1, log, log_every=30.0):
    v, b = inc.v, len(inc.blocks)
    best_J = inc.J()
    best = (list(inc.blocks), dict(inc.Nt))
    start = time.time()
    moves = acc = 0
    last = start
    while True:
        el = time.time() - start
        if el >= seconds:
            break
        T = T0 * (T1 / T0) ** (el / seconds)
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
            log(f"  [exact] t={el:6.1f}s T={T:9.2f} J={inc.J():.1f} best={best_J:.1f} "
                f"N={inc.Nt} moves={moves} acc={acc}")
    return best, {"moves": moves, "accepted": acc, "T0": T0, "T1": T1}


def load_seed(kind, v):
    if kind == "greedy":
        return []
    if kind.startswith("lr:"):
        sys.path.insert(0, str(HERE.parents[2]))  # rădăcina proiectului
        from covering.lr_schemes import lr_schemes
        _, g, code = kind.split(":")
        sch = [s for s in lr_schemes(g) if s.code == int(code)][0]
        if sch.n > v:
            raise ValueError("seed scheme larger than pool")
        return [sum(1 << (p - 1) for p in r) for r in sch.variants]
    path = Path(kind)
    if not path.is_absolute():
        path = HERE / path  # joburile dau runs/<nume>.json, relativ la acest folder
    data = json.loads(path.read_text())
    return [sum(1 << (p - 1) for p in r) for r in data["blocks"] if max(r) <= v]


def main():
    game, budget, v, seconds, seed, out = sys.argv[1:7]
    seed_kind = sys.argv[7] if len(sys.argv) > 7 else "greedy"
    lam3 = float(sys.argv[8]) if len(sys.argv) > 8 else 0.01
    lam5 = float(sys.argv[9]) if len(sys.argv) > 9 else 0.01
    w4mult = float(sys.argv[10]) if len(sys.argv) > 10 else 1.0
    hard_pack = len(sys.argv) > 11 and sys.argv[11] == '1'
    budget, v, seconds, seed = int(budget), int(v), float(seconds), int(seed)
    pick, draw_n, max_num = L.GAMES[game]
    rng = random.Random(seed)
    t0 = time.time()

    def log(msg):
        print(msg, flush=True)

    def blk(ms):
        return [[p + 1 for p in range(v) if m >> p & 1] for m in ms]

    masks = greedy_init(v, pick, budget, rng, load_seed(seed_kind, v))
    g_exact = L.exact(v, blk(masks), game)
    log(f"{game} b={budget} v={v} seed={seed_kind}: start P4={g_exact['P'][4]*100:.6f}% "
        f"cov4={g_exact['covered4']} ({time.time()-t0:.1f}s)")
    if hard_pack:
        assert g_exact["covered4"] == len(masks) * comb(pick, 4), "hard_pack needs a perfect 4-packing seed"
    (sS, smasks, sD), sstats = surrogate_sa(
        v, pick, draw_n, max_num, masks, (1.0 if hard_pack else 0.7) * seconds, rng,
        lam3=lam3, lam5=lam5, log=log, w4mult=w4mult, hard_pack=hard_pack)
    s_exact = L.exact(v, blk(smasks), game)
    log(f"  surrogate done: P4={s_exact['P'][4]*100:.6f}% {sD} moves={sstats['moves']} "
        f"({time.time()-t0:.1f}s)")
    inc = Inc(v, pick, draw_n, max_num, smasks, {4: 1.0, 3: lam3, 5: lam5})
    W4 = float(inc.wk[4])
    if hard_pack:  # the exact phase could break the packing: keep the surrogate result
        stats, rounds = {"skipped": "hard_pack"}, 0
    else:
        remaining = max(5.0, seconds - (time.time() - t0))
        (best_blocks, _bn), stats = exact_anneal(inc, remaining, rng, 0.3 * W4, 0.01 * W4, log)
        inc.set_blocks(best_blocks)
        rounds = descend(inc, rng)
    blocks = blk(inc.blocks)
    ex = L.exact(v, blocks, game)
    for t in inc.ts:
        assert inc.Nt[t] == ex["num"][t], (t, inc.Nt[t], ex["num"][t])
    res = {
        "game": game, "budget": budget, "v": v, "seed": seed, "seconds": seconds,
        "lam3": lam3, "lam5": lam5, "w4mult": w4mult, "hard_pack": hard_pack, "seed_kind": seed_kind,
        "start_P": g_exact["P"], "start_covered4": g_exact["covered4"],
        "surrogate_P": s_exact["P"], "surrogate_stats": sstats,
        "P": ex["P"], "num": ex["num"], "covered4": ex["covered4"], "frac4": ex["frac4"],
        "covered3": ex["covered3"], "frac3": ex["frac3"],
        "exact_stats": stats, "descent_rounds": rounds, "wall": round(time.time() - t0, 1),
        "blocks": sorted(blocks),
    }
    Path(out).write_text(json.dumps(res, indent=0), "utf-8")
    log(f"DONE {game} b={budget} v={v} seed={seed_kind} P4={ex['P'][4]*100:.6f}% "
        f"P3={ex['P'][3]*100:.6f}% cov4={ex['covered4']}/{comb(v,4)} wall={res['wall']}s")


if __name__ == "__main__":
    main()
