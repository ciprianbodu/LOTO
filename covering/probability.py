"""Exact hit counts and probabilities for overlapping tickets under uniform draws."""

from functools import lru_cache
from math import comb, isfinite


def _integer(value):
    try:
        numeric = float(value)
        if not isfinite(numeric) or not numeric.is_integer():
            raise ValueError("Lottery numbers and geometry must be integers")
        return int(numeric)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Lottery numbers and geometry must be integers") from exc


def _wheel_masks(pool, tickets):
    pool = tuple(_integer(n) for n in pool)
    if not 0 < len(pool) <= 16 or len(set(pool)) != len(pool):
        raise ValueError("Pool must contain 1..16 distinct numbers")
    positions = {n: i for i, n in enumerate(sorted(pool))}
    masks = set()
    for ticket in tickets:
        numbers = tuple(_integer(n) for n in ticket)
        if (
            not numbers
            or len(set(numbers)) != len(numbers)
            or any(n not in positions for n in numbers)
        ):
            raise ValueError("Ticket must contain distinct pool numbers")
        masks.add(sum(1 << positions[n] for n in numbers))
    return pool, tuple(sorted(masks))


@lru_cache(maxsize=64)
def _hit_counts(v: int, masks: tuple[int, ...]):
    """counts[h][t] = h-subsets with at least t hits on one ticket.

    Counts use integers, including every intersection size. Comparing them
    componentwise proves non-decreasing unconditional odds for every uniform
    draw geometry: all outside-pool completion weights are non-negative.
    """
    best = bytearray(1 << v)
    for ticket in masks:
        sub = ticket
        while sub:
            best[sub] = sub.bit_count()
            sub = (sub - 1) & ticket
    for mask in range(1, len(best)):
        if best[mask] == mask.bit_count():
            continue
        bits = mask
        while bits:
            bit = bits & -bits
            best[mask] = max(best[mask], best[mask ^ bit])
            bits ^= bit
    counts = [[0] * (v + 1) for _ in range(v + 1)]
    for mask, hits in enumerate(best):
        counts[mask.bit_count()][hits] += 1
    for row in counts:
        for t in range(v - 1, -1, -1):
            row[t] += row[t + 1]
    return tuple(tuple(row) for row in counts)


def wheel_hit_profile(pool, tickets):
    """Exact counts indexed by pool-intersection size and hit threshold.

    Pure ticket geometry; labels and scorer values do not imply probabilities.
    Repeated tickets are counted once. Bounded to 16 distinct pool numbers.
    """
    pool, masks = _wheel_masks(pool, tickets)
    return _hit_counts(len(pool), masks)


@lru_cache(maxsize=32)
def _probabilities(v: int, masks: tuple[int, ...], draw_n: int, max_num: int):
    counts = _hit_counts(v, masks)
    outside = max_num - v
    total = comb(max_num, draw_n)
    values = []
    for target in range(1, draw_n + 1):
        pool_count = ticket_count = 0
        for size, row in enumerate(counts):
            rest = draw_n - size
            if 0 <= rest <= outside:
                weight = comb(outside, rest)
                if size >= target:
                    pool_count += comb(v, size) * weight
                if target <= v:
                    ticket_count += row[target] * weight
        values.append((pool_count / total, ticket_count / total))
    return tuple(values)


def wheel_hit_probabilities(pool, tickets, draw_n: int, max_num: int) -> dict:
    """P(at least t hits) on the pool and on at least one ticket, t=1..draw_n.

    All overlapping tickets are counted together, without independence
    assumptions. 5/40 uses six drawn numbers and five-number tickets; prizes
    and Joker's second urn must be handled separately by the caller.
    Invalid or oversized inputs are rejected, including fractional numbers.
    """
    pool, masks = _wheel_masks(pool, tickets)
    draw_n, max_num = _integer(draw_n), _integer(max_num)
    if (
        not 0 < draw_n <= max_num
        or len(pool) > max_num
        or any(not 1 <= n <= max_num for n in pool)
    ):
        raise ValueError("Invalid draw or pool geometry")
    values = _probabilities(len(pool), masks, draw_n, max_num)
    return {
        "pool": {t: values[t - 1][0] for t in range(1, draw_n + 1)},
        "ticket": {t: values[t - 1][1] for t in range(1, draw_n + 1)},
    }
