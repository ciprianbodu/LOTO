"""Exact wheel probabilities for pools of up to 20 positions (2^v subset DP),
an independent brute-force cross-check, and upper bounds.

Positions are 0..v-1 internally; designs on disk use 1..v.
"""
from __future__ import annotations

import itertools
from math import comb

import numpy as np

GAMES = {
    # name: (pick = ticket size, draw_n, max_num)
    "6/49": (6, 6, 49),
    "5/40": (5, 6, 40),
    "joker": (5, 5, 45),
}


def masks_of(blocks) -> list[int]:
    """blocks given on positions 1..v -> bitmasks (bit p-1)."""
    return sorted({sum(1 << (int(p) - 1) for p in b) for b in blocks})


def counts_dp(v: int, masks) -> np.ndarray:
    """cum[k][t] = number of k-subsets of the v positions whose best
    intersection with a ticket is >= t (k, t = 0..v). 2^v subset DP:
    downward closure of the ticket family, then max-propagation upward."""
    size = 1 << v
    fam = np.zeros(size, dtype=bool)
    fam[np.fromiter(masks, dtype=np.int64)] = True
    for i in range(v):  # every subset of a ticket is in the family
        a = fam.reshape(-1, 2, 1 << i)
        a[:, 0, :] |= a[:, 1, :]
    pc = np.bitwise_count(np.arange(size, dtype=np.uint32)).astype(np.int8)
    best = np.where(fam, pc, 0).astype(np.int8)
    for i in range(v):  # best[m] = max over submasks of fam-popcount
        a = best.reshape(-1, 2, 1 << i)
        np.maximum(a[:, 1, :], a[:, 0, :], out=a[:, 1, :])
    hist = np.bincount(
        pc.astype(np.int64) * (v + 1) + best.astype(np.int64),
        minlength=(v + 1) * (v + 1),
    ).reshape(v + 1, v + 1)
    cum = hist[:, ::-1].cumsum(axis=1)[:, ::-1]
    return cum.astype(np.int64)


def counts_brute(v: int, masks, kmax: int) -> np.ndarray:
    """Same table for k <= kmax, by enumerating k-subsets and taking the max
    popcount against every ticket (independent algorithm)."""
    t = np.array(list(masks), dtype=np.uint32)
    cum = np.zeros((kmax + 1, v + 1), dtype=np.int64)
    for k in range(kmax + 1):
        if k > v:
            break
        subs = np.array(
            [sum(1 << p for p in c) for c in itertools.combinations(range(v), k)],
            dtype=np.uint32,
        )
        best = np.zeros(len(subs), dtype=np.int64)
        for start in range(0, len(t), 64):
            chunk = t[start:start + 64]
            hits = np.bitwise_count(subs[:, None] & chunk[None, :]).max(axis=1)
            best = np.maximum(best, hits)
        h = np.bincount(best, minlength=v + 1)
        cum[k] = h[::-1].cumsum()[::-1]
    return cum


def numerators(v: int, cum, draw_n: int, max_num: int) -> dict[int, int]:
    """{t: number of draws (of C(max_num, draw_n)) with >= t on one ticket}."""
    out = {}
    outside = max_num - v
    for t in range(1, draw_n + 1):
        s = 0
        for k in range(t, min(v, draw_n) + 1):
            rest = draw_n - k
            if 0 <= rest <= outside:
                s += int(cum[k][t]) * comb(outside, rest)
        out[t] = s
    return out


def pool_numerators(v: int, draw_n: int, max_num: int) -> dict[int, int]:
    outside = max_num - v
    return {
        t: sum(
            comb(v, k) * comb(outside, draw_n - k)
            for k in range(t, min(v, draw_n) + 1)
            if 0 <= draw_n - k <= outside
        )
        for t in range(1, draw_n + 1)
    }


def exact(v: int, blocks, game: str, use_brute: bool = False) -> dict:
    pick, draw_n, max_num = GAMES[game]
    m = masks_of(blocks)
    cum = counts_brute(v, m, draw_n) if use_brute else counts_dp(v, m)
    num = numerators(v, cum, draw_n, max_num)
    total = comb(max_num, draw_n)
    t4 = cum[4][4] if v >= 4 else 0
    return {
        "v": v,
        "variants": len(m),
        "num": {t: num[t] for t in range(3, min(pick, draw_n) + 1)},
        "P": {t: num[t] / total for t in range(3, min(pick, draw_n) + 1)},
        "covered4": int(t4),
        "frac4": int(t4) / comb(v, 4),
        "covered3": int(cum[3][3]),
        "frac3": int(cum[3][3]) / comb(v, 3),
        "cum": cum,
    }


# --------------------------------------------------------------------------- #
# Upper bounds
# --------------------------------------------------------------------------- #
def ub_repo(n, size, pick, t, draw_n, max_num) -> float:
    """Verbatim re-implementation of covering.lr_schemes._upper_bound."""
    total = comb(max_num, draw_n)
    d_max = size if t == pick else min(comb(n, t), size * comb(pick, t))
    prob = 0.0
    for k in range(t, min(n, draw_n) + 1):
        rest = draw_n - k
        if rest > max_num - n:
            continue
        cap = min(comb(n, k), d_max * comb(n - t, k - t))
        if k == pick and t == pick - 1:
            cap = min(cap, (n - t) * d_max - (pick - 1) * size)
        prob += cap * comb(max_num - n, rest) / total
    return prob


def derived_cover_cap(n, size, pick, t) -> int:
    """Max number of distinct t-subsets that `size` distinct pick-blocks on n
    points can cover, bounded through the (t-1)-subsets: for a (t-1)-set X in
    r_X blocks, at most min(n-t+1, (pick-t+1) r_X) covered t-sets contain X;
    sum_X r_X = size*C(pick, t-1); each covered t-set is counted t times.
    The concave allocation is solved exactly (greedy)."""
    if t == pick:
        return min(comb(n, t), size)
    if t <= 1:
        return min(comb(n, t), size * comb(pick, t))
    M = comb(n, t - 1)
    R = size * comb(pick, t - 1)
    cap = n - t + 1
    a = pick - t + 1
    r0 = cap // a
    full = min(R, M * r0)
    value = full * a
    rem = R - full
    if rem > 0 and cap > a * r0:
        value += min(rem, M) * (cap - a * r0)
    return min(comb(n, t), size * comb(pick, t), value // t)


def ub_tight(n, size, pick, t, draw_n, max_num, return_terms=False):
    """The repo bound with two extra valid caps:
    - D (covered t-sets) also <= derived_cover_cap (Johnson-type, one level);
    - per k, the union bound over BLOCKS: #k-sets meeting some block in >= t
      points <= size * sum_{j>=t} C(pick,j) C(n-pick,k-j).
    Every term is a valid cap for its k, so the sum is a valid upper bound."""
    total = comb(max_num, draw_n)
    d_max = derived_cover_cap(n, size, pick, t)
    num = 0
    terms = {}
    for k in range(t, min(n, draw_n) + 1):
        rest = draw_n - k
        if rest > max_num - n:
            continue
        cap = min(comb(n, k), d_max * comb(n - t, k - t))
        if k == pick and t == pick - 1:
            cap = min(cap, (n - t) * d_max - (pick - 1) * size)
        block = size * sum(
            comb(pick, j) * comb(n - pick, k - j) for j in range(t, min(pick, k) + 1)
        )
        cap = min(cap, block)
        terms[k] = cap
        num += cap * comb(max_num - n, rest)
    if return_terms:
        return num, terms
    return num / total


def ub_tight_num(n, size, pick, t, draw_n, max_num) -> int:
    return ub_tight(n, size, pick, t, draw_n, max_num, return_terms=True)[0]
