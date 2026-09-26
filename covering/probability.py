"""Exact hit probabilities of the actual tickets under a uniform lottery draw.

No independent-ticket approximation: overlapping tickets are counted once per
draw. Pool hits and ticket hits remain separate, including conditional covers.
"""
from functools import lru_cache
from math import comb


@lru_cache(maxsize=32)
def _probabilities(v: int, masks: tuple[int, ...], draw_n: int, max_num: int):
    # best[S] = max_ticket |S intersect ticket|, for all subsets of the pool.
    # First mark subsets contained in a ticket, then propagate upwards. Cost is
    # O(v * 2**v + tickets * 2**pick), bounded by the UI's 16-number pool.
    best = bytearray(1 << v)
    for ticket in masks:
        sub = ticket
        while sub:
            best[sub] = sub.bit_count()
            sub = (sub - 1) & ticket
    for mask in range(1, len(best)):
        size = mask.bit_count()
        if best[mask] == size:
            continue
        bits = mask
        while bits:
            bit = bits & -bits
            best[mask] = max(best[mask], best[mask ^ bit])
            bits ^= bit

    ticket_counts = [0] * (draw_n + 1)
    pool_counts = [0] * (draw_n + 1)
    outside = max_num - v
    for mask, hits in enumerate(best):
        size = mask.bit_count()
        rest = draw_n - size
        if 0 <= rest <= outside:
            count = comb(outside, rest)
            ticket_counts[hits] += count
            pool_counts[size] += count
    total = comb(max_num, draw_n)
    return tuple(
        (sum(pool_counts[t:]) / total, sum(ticket_counts[t:]) / total)
        for t in range(1, draw_n + 1)
    )


def wheel_hit_probabilities(pool, tickets, draw_n: int, max_num: int) -> dict:
    """P(at least t hits), for pool and at least one ticket, t=1..draw_n.

    Tickets contain only main-urn numbers. For 5/40 the app uses draw_n=6
    (hits counted on all six drawn numbers, 5-number tickets); no prize is
    inferred.
    Joker's second urn must be handled separately by the caller.
    Invalid/oversized data are rejected instead of publishing misleading odds.
    """
    pool = tuple(int(n) for n in pool)
    if not 0 < len(pool) <= 16 or len(set(pool)) != len(pool):
        raise ValueError('Pool must contain 1..16 distinct numbers')
    if not 0 < draw_n <= max_num or any(not 1 <= n <= max_num for n in pool):
        raise ValueError('Invalid draw or pool geometry')
    positions = {n: i for i, n in enumerate(sorted(pool))}
    masks = set()
    for ticket in tickets:
        numbers = tuple(int(n) for n in ticket)
        if (not numbers or len(set(numbers)) != len(numbers)
                or any(n not in positions for n in numbers)):
            raise ValueError('Ticket must contain distinct pool numbers')
        masks.add(sum(1 << positions[n] for n in numbers))
    values = _probabilities(len(pool), tuple(sorted(masks)), draw_n, max_num)
    return {
        'pool': {t: values[t - 1][0] for t in range(1, draw_n + 1)},
        'ticket': {t: values[t - 1][1] for t in range(1, draw_n + 1)},
    }
