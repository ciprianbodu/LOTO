"""Bounded geometry swaps that improve high hits on complete four-covers.

No historical data or scorer inference enters the search. Pool order is the
caller's canonical score order; number labels only map the resulting geometry.
"""
from __future__ import annotations

from collections import Counter
from functools import lru_cache
from itertools import combinations
from math import comb

from covering.probability import wheel_hit_profile

_MAX_CANDIDATES = 16384
_MAX_PASSES = 2


@lru_cache(maxsize=8192)
def _supersets(v: int, required: int) -> int:
    """Bit i is set when subset-mask i contains every required position."""
    value = 1 << required
    for pos in range(v):
        bit = 1 << pos
        if not required & bit:
            value |= value << bit
    return value


@lru_cache(maxsize=16)
def _layers(v: int) -> tuple[int, ...]:
    result = [0] * (v + 1)
    for subset in range(1 << v):
        result[subset.bit_count()] |= 1 << subset
    return tuple(result)


def _subsets(mask: int, target: int, v: int):
    positions = [i for i in range(v) if mask & (1 << i)]
    return (sum(1 << i for i in subset) for subset in combinations(positions, target))


@lru_cache(maxsize=512)
def _events(v: int, mask: int, target: int) -> int:
    result = 0
    for subset in _subsets(mask, target, v):
        result |= _supersets(v, subset)
    return result


def _high_profile(v: int, events: tuple[int, int]) -> tuple[int, ...]:
    return tuple(
        (event & _layers(v)[h]).bit_count()
        for event in events
        for h in range(5, v + 1)
    )


@lru_cache(maxsize=16)
def _improve_positions(
    v: int, pick: int, masks: tuple[int, ...], draw_n: int
) -> tuple[tuple[int, ...], int, int]:
    targetsets = {mask: tuple(_subsets(mask, 4, v)) for mask in masks}
    counts = Counter(target for mask in masks for target in targetsets[mask])
    if len(counts) != comb(v, 4):
        return masks, 0, 0
    blocks = tuple(sum(1 << i for i in b) for b in combinations(range(v), pick))
    selected = list(masks)
    event5 = event6 = 0
    for mask in selected:
        event5 |= _events(v, mask, 5)
        event6 |= _events(v, mask, 6)
    current_events = (event5, event6)
    current = _high_profile(v, current_events)
    swaps = evaluated = 0
    possible_sizes = min(draw_n, v) - 4

    for _ in range(_MAX_PASSES):
        changed = False
        for slot in range(len(selected)):
            if evaluated >= _MAX_CANDIDATES:
                break
            old = selected[slot]
            required = 0
            for target in targetsets[old]:
                if counts[target] == 1:
                    required |= target
            # Every unique four-target must remain on the replacement ticket.
            if required.bit_count() >= pick:
                continue
            others5 = others6 = 0
            occupied = set(selected)
            occupied.remove(old)
            for i, mask in enumerate(selected):
                if i != slot:
                    others5 |= _events(v, mask, 5)
                    others6 |= _events(v, mask, 6)
            best, best_profile, best_events = old, current, current_events
            for mask in blocks:
                if evaluated >= _MAX_CANDIDATES:
                    break
                if mask == old or mask in occupied or mask & required != required:
                    continue
                evaluated += 1
                new_events = (
                    others5 | _events(v, mask, 5),
                    others6 | _events(v, mask, 6),
                )
                candidate = _high_profile(v, new_events)
                if any(a < b for a, b in zip(candidate, current)):
                    continue
                if not any(
                    candidate[i] > current[i] for i in range(possible_sizes)
                ):
                    continue
                # Geometry-only quality. Acceptance already preserves every
                # intersection size, so no lottery weighting is needed.
                if (sum(candidate[:possible_sizes]), candidate) > (
                    sum(best_profile[:possible_sizes]), best_profile
                ):
                    best, best_profile, best_events = mask, candidate, new_events
            if best != old:
                for target in targetsets[old]:
                    counts[target] -= 1
                targetsets[best] = tuple(_subsets(best, 4, v))
                for target in targetsets[best]:
                    counts[target] += 1
                selected[slot] = best
                current, current_events = best_profile, best_events
                swaps += 1
                changed = True
        if not changed or evaluated >= _MAX_CANDIDATES:
            break
    return tuple(selected), swaps, evaluated


def improve_higher_hits(pool, tickets, draw_n: int = 6):
    """Return (wheel, audit), keeping count and every hit-profile component.

    Only complete classical four-covers are searched. Bounds: 5..16 pool
    numbers, 5/6-number tickets, at most 512 distinct tickets, two passes and
    16,384 eligible replacements. Outside those bounds the supplied wheel is
    preserved. A five-number draw with five-number tickets cannot improve its
    five-hit probability at a fixed distinct count, so it is bypassed.

    ``pool`` must be in the canonical score order when used in production.
    In-memory caches contain geometry only and have fixed maximum sizes.
    The final full profile is independently certified after mapping back.
    """
    pool = tuple(pool)
    baseline = [list(ticket) for ticket in tickets]
    audit = {"swaps": 0, "candidates_evaluated": 0, "applied": False}
    if not baseline:
        return baseline, audit
    pick = len(baseline[0])
    if not (
        5 <= len(pool) <= 16
        and len(set(pool)) == len(pool)
        and pick in (5, 6)
        and pick <= len(pool)
        and draw_n in (5, 6)
        and draw_n >= pick
        and 1 <= len(baseline) <= 512
        and all(len(set(ticket)) == len(ticket) == pick for ticket in baseline)
        and all(set(ticket) <= set(pool) for ticket in baseline)
    ):
        return baseline, audit
    if pick == draw_n == 5:
        return baseline, audit
    positions = {number: i for i, number in enumerate(pool)}
    masks = tuple(sum(1 << positions[n] for n in ticket) for ticket in baseline)
    if len(set(masks)) != len(masks):
        return baseline, audit
    improved, swaps, evaluated = _improve_positions(len(pool), pick, masks, draw_n)
    audit["candidates_evaluated"] = evaluated
    if not swaps:
        return baseline, audit
    candidate = [
        sorted(pool[i] for i in range(len(pool)) if mask & (1 << i))
        for mask in improved
    ]
    if len(candidate) != len(baseline) or len(set(improved)) != len(improved):
        return baseline, audit
    old_profile = wheel_hit_profile(pool, baseline)
    new_profile = wheel_hit_profile(pool, candidate)
    if any(
        new < old
        for new_row, old_row in zip(new_profile, old_profile)
        for new, old in zip(new_row, old_row)
    ):
        return baseline, audit
    if not any(
        new_profile[h][5] > old_profile[h][5]
        for h in range(5, min(draw_n, len(pool)) + 1)
    ):
        return baseline, audit
    audit.update({"swaps": swaps, "applied": True})
    return candidate, audit
