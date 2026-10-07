"""Bounded one-ticket swaps accepted only under exact hit-profile dominance.

Generic companion of ``higher_hits``: works for any classical guarantee, for
lotto designs "t if p" and for budgeted wheels of any size. A swap keeps the
number of distinct tickets and is accepted only when the exact profile of
``covering.probability.wheel_hit_profile`` does not fall at ANY threshold and
ANY pool-intersection size and rises strictly at a 3+/4+/5+ count that a draw
can realise. Componentwise dominance alone preserves every guarantee:

* classical t-cover:   counts[t][t] == C(v, t) cannot fall;
* lotto "t if p":      counts[p][t] == C(v, p) cannot fall;
* pool numbers played: counts[1][1] cannot fall.

Geometry only. Pool positions follow the caller's canonical score order; no
history, score magnitude or probability claim enters the search. The result
is certified again with ``wheel_hit_profile`` after mapping back to numbers.
"""

from __future__ import annotations

from functools import lru_cache
from itertools import combinations
from math import comb

from covering.higher_hits import _layers, _supersets
from covering.probability import wheel_hit_profile

MAX_CANDIDATES = 25000
MAX_PASSES = 2
MAX_TICKETS = 512
TARGETS = (3, 4, 5)


@lru_cache(maxsize=16)
def _position_sets(v: int) -> tuple[int, ...]:
    """Entry p: bitset of the pool subsets that contain position p."""
    return tuple(_supersets(v, 1 << p) for p in range(v))


def _add_position(level: list[int], x: int, top: int) -> list[int]:
    """Event levels after one more ticket position (``x`` = its subsets).

    A subset meets the longer ticket in >= t places when it met the shorter
    one in >= t, or in >= t-1 and contains the new position."""
    level = list(level)
    for t in range(top, 1, -1):
        level[t] |= level[t - 1] & x
    level[1] |= x
    return level


def _threshold_events(v: int, mask: int, pick: int) -> list[int]:
    """``ev[t]``, t = 0..pick: pool subsets meeting ``mask`` in >= t places."""
    xs = _position_sets(v)
    level = [(1 << (1 << v)) - 1] + [0] * pick
    k = 0
    for p in range(v):
        if mask >> p & 1:
            k += 1
            level = _add_position(level, xs[p], min(k, pick))
    return level


def _row(v: int, event: int, t: int) -> tuple[int, ...]:
    layers = _layers(v)
    return tuple((event & layers[h]).bit_count() for h in range(t, v + 1))


@lru_cache(maxsize=32)
def _improve(
    v: int,
    pick: int,
    masks: tuple[int, ...],
    draw_n: int,
    frozen: int,
    max_candidates: int,
) -> tuple[tuple[int, ...], int, int]:
    thresholds = tuple(range(1, pick + 1))
    layers = _layers(v)
    everything = (1 << (1 << v)) - 1
    selected = list(masks)
    tickets = [_threshold_events(v, m, pick) for m in selected]
    current = {t: 0 for t in thresholds}
    for ev in tickets:
        for t in thresholds:
            current[t] |= ev[t]
    rows = {t: _row(v, current[t], t) for t in thresholds}
    full = {t: comb(v, t) for t in thresholds}
    # Strict gain only where a draw of ``draw_n`` numbers can land.
    strict = {
        t: min(draw_n, v) - t + 1 for t in TARGETS if t <= pick and t <= draw_n
    }
    # Rows compared high t first, layers ascending (the tie-break order).
    order = tuple(sorted(thresholds, reverse=True))
    combos = tuple(combinations(range(v), pick))
    blocks = tuple(sum(1 << i for i in b) for b in combos)
    xs = _position_sets(v)
    occupied = set(selected)
    swaps = evaluated = 0
    for _ in range(MAX_PASSES):
        changed = False
        for slot in range(frozen, len(selected)):
            if evaluated >= max_candidates:
                break
            old = selected[slot]
            others = {t: 0 for t in thresholds}
            for i, ev in enumerate(tickets):
                if i != slot:
                    for t in thresholds:
                        others[t] |= ev[t]
            # A complete threshold row must stay complete: every t-subset
            # covered only by the old ticket has to be on its replacement.
            required = 0
            for t in thresholds:
                if rows[t][0] == full[t]:
                    lost = current[t] & ~others[t] & layers[t]
                    while lost:
                        low = lost & -lost
                        required |= low.bit_length() - 1
                        lost ^= low
            if required.bit_count() >= pick:
                continue
            # A candidate's row = the other tickets' row + what it adds outside
            # them, layer by layer. It can fall below the current row only
            # where removing the old ticket loses subsets.
            base = {t: _row(v, others[t], t) for t in thresholds}
            free = {
                t: [(everything ^ others[t]) & layers[h] for h in range(t, v + 1)]
                for t in thresholds
            }
            checks = [
                (t, i) for t in order for i in range(v - t + 1) if rows[t][i] > base[t][i]
            ]
            checked = {pair: k for k, pair in enumerate(checks)}
            gains = [
                (t, i, checked.get((t, i))) for t, n in strict.items() for i in range(n)
            ]
            best = best_rows = None
            best_gain = 0
            # Blocks come in lexicographic order: consecutive ones share a
            # prefix of positions, whose event levels are kept.
            states = [[everything] + [0] * pick] + [None] * pick
            prev = None
            for index, block in enumerate(blocks):
                if evaluated >= max_candidates:
                    break
                if block in occupied or block & required != required:
                    continue
                evaluated += 1
                combo = combos[index]
                depth = 0
                if prev is not None:
                    while combo[depth] == prev[depth]:
                        depth += 1
                for d in range(depth, pick):
                    states[d + 1] = _add_position(states[d], xs[combo[d]], d + 1)
                prev = combo
                ev = states[pick]
                values = []
                for t, i in checks:
                    value = base[t][i] + (ev[t] & free[t][i]).bit_count()
                    if value < rows[t][i]:
                        break
                    values.append(value)
                else:
                    gain = 0
                    for t, i, k in gains:
                        if k is None:
                            value = base[t][i] + (ev[t] & free[t][i]).bit_count()
                        else:
                            value = values[k]
                        gain += value - rows[t][i]
                    if gain <= 0 or (best is not None and gain < best_gain):
                        continue
                    if best is not None and gain == best_gain:
                        # Equal gain: the rows decide, high t first, layers
                        # ascending; equal rows keep the earlier block.
                        greater = False
                        for t in order:
                            for i, kept in enumerate(best_rows[t]):
                                value = base[t][i] + (ev[t] & free[t][i]).bit_count()
                                if value != kept:
                                    greater = value > kept
                                    break
                            else:
                                continue
                            break
                        if not greater:
                            continue
                    best, best_gain = block, gain
                    best_rows = {
                        t: tuple(
                            base[t][i] + (ev[t] & free[t][i]).bit_count()
                            for i in range(v - t + 1)
                        )
                        for t in thresholds
                    }
            if best is not None:
                occupied.discard(old)
                occupied.add(best)
                selected[slot] = best
                tickets[slot] = _threshold_events(v, best, pick)
                current = {t: others[t] | tickets[slot][t] for t in thresholds}
                rows = {t: _row(v, current[t], t) for t in thresholds}
                swaps += 1
                changed = True
        if not changed or evaluated >= max_candidates:
            break
    return tuple(selected), swaps, evaluated


def improve_hit_profile(
    pool,
    tickets,
    draw_n: int | None = None,
    frozen: int = 0,
    max_candidates: int = MAX_CANDIDATES,
):
    """Return (wheel, audit); the wheel dominates the input profile exactly.

    ``pool`` must be in canonical score order (best first). The first
    ``frozen`` tickets are never changed (physical tickets keep their base
    wheel). Bounds: 5..16 pool numbers, picks 3..6, 1..512 distinct tickets,
    two passes and ``max_candidates`` evaluated replacements. Outside the
    bounds, or without a strict 3+/4+/5+ gain, the input is returned.
    """
    pool = tuple(int(n) for n in pool)
    baseline = [sorted(int(n) for n in ticket) for ticket in tickets]
    audit = {"swaps": 0, "candidates_evaluated": 0, "applied": False}
    if not baseline:
        return baseline, audit
    pick = len(baseline[0])
    draw_n = pick if draw_n is None else int(draw_n)
    if not (
        5 <= len(pool) <= 16
        and len(set(pool)) == len(pool)
        and 3 <= pick <= 6
        and pick < len(pool)
        and draw_n >= 3
        and 1 <= len(baseline) <= MAX_TICKETS
        and 0 <= int(frozen) < len(baseline)
        and all(len(set(t)) == len(t) == pick for t in baseline)
        and all(set(t) <= set(pool) for t in baseline)
    ):
        return baseline, audit
    positions = {n: i for i, n in enumerate(pool)}
    masks = tuple(sum(1 << positions[n] for n in t) for t in baseline)
    if len(set(masks)) != len(masks):
        return baseline, audit
    improved, swaps, evaluated = _improve(
        len(pool), pick, masks, draw_n, int(frozen), int(max_candidates)
    )
    audit["candidates_evaluated"] = evaluated
    if not swaps:
        return baseline, audit
    candidate = [
        sorted(pool[i] for i in range(len(pool)) if mask >> i & 1)
        for mask in improved
    ]
    if candidate[:frozen] != baseline[:frozen]:
        return baseline, audit
    old = wheel_hit_profile(pool, baseline)
    new = wheel_hit_profile(pool, candidate)
    if any(a < b for ra, rb in zip(new, old) for a, b in zip(ra, rb)):
        return baseline, audit
    if not any(
        new[h][t] > old[h][t]
        for t in TARGETS
        if t <= min(pick, draw_n)
        for h in range(t, min(draw_n, len(pool)) + 1)
    ):
        return baseline, audit
    audit.update({"swaps": swaps, "applied": True})
    return candidate, audit
