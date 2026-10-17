"""Exact hill climbing on ticket hit chances above hitcover's budget range.

Hitcover optimises budgets of 1..64 tickets; larger budgets kept the canonical
greedy wheel, whose chances could even fall when one ticket was added (65 vs
64). This search starts from the incumbent wheel and moves one number of one
ticket at a time. A move is accepted only when the exact probability of at
least t hits on some ticket (uniform draw of ``draw_n`` numbers from
``max_num``) does not fall for ANY t, the tickets stay distinct and every pool
position stays on a ticket. Moves that change nothing are accepted too, so the
search can cross plateaus.

The caller certifies the result like hitcover: the exact profile of
``covering.probability.wheel_hit_profile`` may not fall at any threshold or
intersection size, and it must rise at a 3+/4+/5+ count a draw can realise.
Componentwise dominance keeps every guarantee (classical t-cover, lotto
"t if p", pool numbers played).

Geometry only: pool positions follow the caller's canonical score order; no
history, score magnitude or probability claim enters the search. Deterministic:
a fixed move budget and a random generator seeded from the geometry.
"""

from __future__ import annotations

import random
from functools import lru_cache
from math import comb

import numpy as np

from covering.probability import wheel_hit_profile

MIN_TICKETS = 65  # hitcover handles 1..64
MAX_TICKETS = 512
MAX_POOL = 16
MOVES = 12000
TARGETS = (3, 4, 5)


@lru_cache(maxsize=8)
def _subsets(v: int, draw_n: int, max_num: int):
    """Pool subsets a draw can hit (sizes 1..draw_n), with exact draw counts."""
    masks = np.arange(1, 1 << v, dtype=np.uint32)
    sizes = np.bitwise_count(masks).astype(np.int64)
    outside = max_num - v
    keep = (sizes <= draw_n) & (draw_n - sizes <= outside)
    masks, sizes = masks[keep], sizes[keep]
    weights = np.array([comb(outside, draw_n - int(s)) for s in sizes], dtype=np.int64)
    member = np.stack([((masks >> p) & 1).astype(bool) for p in range(v)])
    return masks, sizes, weights, member


def _climb(
    v: int,
    pick: int,
    draw_n: int,
    max_num: int,
    guarantee: int,
    tickets: tuple[int, ...],
    moves: int,
) -> tuple[tuple[int, ...], int]:
    masks, sizes, weights, member = _subsets(v, draw_n, max_num)
    top = min(pick, draw_n)
    selected = list(tickets)
    inter = np.stack([np.bitwise_count(masks & np.uint32(m)) for m in selected]).astype(
        np.int8
    )
    cnt = np.zeros((top + 1, len(masks)), dtype=np.int32)
    for t in range(1, top + 1):
        cnt[t] = (inter >= t).sum(axis=0)
    occupied = [0] * v
    for m in selected:
        for p in range(v):
            occupied[p] += m >> p & 1
    present = set(selected)
    targets = np.flatnonzero(sizes == guarantee)
    pairs: dict[tuple[int, int], tuple[np.ndarray, np.ndarray]] = {}
    rng = random.Random(f"budget-climb|{v}|{pick}|{draw_n}|{max_num}|{guarantee}|{len(selected)}")
    accepted = 0
    for _ in range(moves):
        # Aim at a guarantee subset no ticket meets yet: a ticket that meets it
        # in all but one place takes the missing number.
        uncovered = -1
        for _try in range(64):
            s = int(targets[rng.randrange(len(targets))])
            if cnt[guarantee, s] == 0:
                uncovered = s
                break
        if uncovered < 0:
            i = rng.randrange(len(selected))
            mask = selected[i]
            x = rng.choice([p for p in range(v) if mask >> p & 1])
            y = rng.choice([p for p in range(v) if not mask >> p & 1])
        else:
            target = int(masks[uncovered])
            near = np.flatnonzero(inter[:, uncovered] == guarantee - 1)
            if not len(near):
                continue
            i = int(near[rng.randrange(len(near))])
            mask = selected[i]
            y = (target & ~mask).bit_length() - 1
            x = rng.choice([p for p in range(v) if mask >> p & 1 and not target >> p & 1])
        if occupied[x] == 1:
            continue
        moved = (mask & ~(1 << x)) | (1 << y)
        if moved in present:
            continue
        key = (x, y)
        if key not in pairs:
            pairs[key] = (
                np.flatnonzero(member[x] & ~member[y]),
                np.flatnonzero(member[y] & ~member[x]),
            )
        lose, gain = pairs[key]
        old_lose, old_gain = inter[i, lose], inter[i, gain]
        c_lose, c_gain = cnt[:, lose], cnt[:, gain]
        w_lose, w_gain = weights[lose], weights[gain]
        ok = True
        for t in range(1, top + 1):
            delta = int(w_gain[(old_gain == t - 1) & (c_gain[t] == 0)].sum()) - int(
                w_lose[(old_lose == t) & (c_lose[t] == 1)].sum()
            )
            if delta < 0:
                ok = False
                break
        if not ok:
            continue
        for t in range(1, top + 1):
            cnt[t, lose[old_lose == t]] -= 1
            cnt[t, gain[old_gain == t - 1]] += 1
        inter[i, lose] -= 1
        inter[i, gain] += 1
        occupied[x] -= 1
        occupied[y] += 1
        present.discard(mask)
        present.add(moved)
        selected[i] = moved
        accepted += 1
    return tuple(selected), accepted


@lru_cache(maxsize=32)
def _climb_cached(v, pick, draw_n, max_num, guarantee, tickets, moves):
    return _climb(v, pick, draw_n, max_num, guarantee, tickets, moves)


def _dominates(candidate, incumbent, v: int, draw_n: int) -> bool:
    if any(a < b for ra, rb in zip(candidate, incumbent) for a, b in zip(ra, rb)):
        return False
    return any(
        candidate[h][t] > incumbent[h][t]
        for t in TARGETS
        if t <= draw_n
        for h in range(t, min(draw_n, v) + 1)
    )


def improve_budget_hits(
    ordered_pool,
    wheel,
    guarantee: int,
    draw_n: int,
    max_num: int,
    moves: int = MOVES,
    start: tuple[int, ...] | None = None,
):
    """Return ``(wheel, audit)``; the incumbent stays unless certified better.

    ``ordered_pool`` is the canonical score order (best first). ``start``, when
    given, is a position wheel (bit p = the p-th ranked number) that does not
    depend on the scores: its climb is computed once per geometry and tried
    first. The incumbent's own climb runs only when that candidate does not
    dominate. The audit says which start won, how many moves were accepted and
    whether the exact profile check passed.
    """
    pool = [int(n) for n in ordered_pool]
    audit = {"applied": False, "accepted_moves": 0, "reason": ""}
    v = len(pool)
    tickets = [sorted(int(n) for n in t) for t in wheel]
    pick = len(tickets[0]) if tickets else 0
    distinct = {tuple(t) for t in tickets}
    if not (
        MIN_TICKETS <= len(tickets) <= MAX_TICKETS
        and pick <= v <= MAX_POOL
        and len(set(pool)) == v
        and len(distinct) == len(tickets)
        and all(len(t) == pick for t in tickets)
        and 1 <= guarantee < pick
        and guarantee <= draw_n
        and v < max_num
    ):
        audit["reason"] = "outside limits"
        return wheel, audit
    index = {n: i for i, n in enumerate(pool)}
    if any(n not in index for t in tickets for n in t):
        audit["reason"] = "ticket outside pool"
        return wheel, audit
    masks = tuple(sum(1 << index[n] for n in t) for t in tickets)
    if any(not any(m >> p & 1 for m in masks) for p in range(v)):
        audit["reason"] = "pool number not on a ticket"
        return wheel, audit
    incumbent = wheel_hit_profile(pool, tickets)
    starts = []
    if start is not None and len(start) == len(masks) and start != masks:
        starts.append(("canonical", tuple(start)))
    starts.append(("incumbent", masks))
    audit["reason"] = "profile not dominant"
    for label, origin in starts:
        found, accepted = _climb_cached(v, pick, draw_n, max_num, guarantee, origin, moves)
        if found == masks:
            continue
        candidate = [sorted(pool[p] for p in range(v) if m >> p & 1) for m in found]
        if len({tuple(t) for t in candidate}) != len(candidate):
            continue
        if _dominates(wheel_hit_profile(pool, candidate), incumbent, v, draw_n):
            audit.update(applied=True, accepted_moves=accepted, start=label, reason="")
            return candidate, audit
    return wheel, audit
