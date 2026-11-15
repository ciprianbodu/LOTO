"""Exact 21-variant Loto 5/40 packing on all forty pool positions.

Two blocks intersect in at most one position. Two variants with four hits
would require at least seven drawn numbers, whereas 5/40 draws six. Thus
their 4+ events are disjoint and the union bound is attained globally:
21 * (C(5,4) * C(35,2) + C(35,1)) / C(40,6).

The frozen position design and the two previous constructor outputs were
independently enumerated over all C(40,6) draws. Stored histograms are used
only after the complete position signature matches. No observed drawing,
scorer fitting, historical hit rate or minimum-size claim enters this stage.
Other budgets and geometries keep their existing construction.
"""

from __future__ import annotations

import hashlib
import operator
from itertools import combinations
from math import comb

from covering.common import _coverage_ratio_pct

_BLOCKS = (
    (1, 2, 3, 4, 5),
    (6, 7, 8, 9, 10),
    (11, 12, 13, 14, 15),
    (16, 17, 18, 19, 20),
    (21, 22, 23, 24, 25),
    (26, 27, 28, 29, 30),
    (31, 32, 33, 34, 35),
    (36, 37, 38, 39, 40),
    (5, 10, 13, 19, 37),
    (8, 18, 25, 28, 33),
    (17, 24, 27, 32, 39),
    (4, 21, 29, 35, 40),
    (6, 16, 23, 31, 38),
    (3, 9, 15, 22, 36),
    (7, 20, 26, 34, 36),
    (2, 11, 24, 30, 37),
    (1, 10, 12, 18, 21),
    (5, 8, 14, 23, 26),
    (7, 12, 25, 27, 38),
    (4, 6, 22, 28, 32),
    (9, 14, 30, 31, 40),
)
_DESIGN_SHA = "1b354b9568911777d2cd98298af540e412af604107528cbf483b3331ff55bd90"
REVISION = "fb540-21-v1-" + _DESIGN_SHA[:12]
_TOTAL = comb(40, 6)
_HISTOGRAM = (0, 11889, 2505656, 1257625, 62475, 735, 0)
_PREVIOUS = {
    4: (
        "d87d9283101c108f1946b871385c220cdf92bd59558f8c193b46c5dcb8ced759",
        (0, 161800, 2723237, 898720, 53888, 735, 0),
    ),
    5: (
        "304f9e960997e878ddacc93f2e11eee781b71ac940fb98aec31c483a6847aad3",
        (0, 180000, 2851840, 762440, 43456, 644, 0),
    ),
}


def _integer(value):
    if isinstance(value, bool):
        raise ValueError("Boolean is not a lottery integer")
    return operator.index(value)


def fixed_budget_540_signature(
    pool_size, pick, guarantee, max_variants, condition=None, draw_n=None, max_num=None
):
    """The narrow construction revision, or None for every unchanged request."""
    try:
        v, k, g, cap, drawn, maximum = map(
            _integer, (pool_size, pick, guarantee, max_variants, draw_n, max_num)
        )
        c = g if condition is None else _integer(condition)
    except (TypeError, ValueError, OverflowError):
        return None
    if (v, k, cap, drawn, maximum) == (40, 5, 21, 6, 40) and g in (4, 5) and c in (0, g):
        return REVISION
    return None


def _positions(ordered_pool, tickets):
    """Validate a complete actual wheel and map it to canonical positions."""
    pool = tuple(_integer(n) for n in ordered_pool)
    if len(pool) != 40 or set(pool) != set(range(1, 41)):
        raise ValueError("The packing requires forty distinct numbers from 1..40")
    positions = {number: i + 1 for i, number in enumerate(pool)}
    rows = []
    for ticket in tickets:
        values = tuple(_integer(n) for n in ticket)
        if len(values) != len(set(values)) or len(values) != 5:
            raise ValueError("Each actual variant must contain five distinct numbers")
        rows.append(tuple(sorted(positions[n] for n in values)))
    if len(rows) != 21 or len(set(rows)) != 21:
        raise ValueError("The packing requires exactly twenty-one distinct variants")
    return pool, tuple(sorted(rows))


def _sha(rows):
    text = "\n".join(" ".join(map(str, row)) for row in rows)
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def fixed_budget_540_certificate(ordered_pool, tickets, *, draw_n=6, max_num=40):
    """Exact probabilities only for this frozen position design, else None.

    ``ordered_pool`` is the position order saved in audit.fixed_budget_540,
    not necessarily the numeric display order of hard_core. Ticket order and
    number order inside a variant are immaterial. Mutated or incomplete
    results are rejected rather than inheriting the certificate by size.
    """
    try:
        if (_integer(draw_n), _integer(max_num)) != (6, 40):
            return None
        pool, rows = _positions(ordered_pool, tickets)
        if _sha(rows) != _DESIGN_SHA:
            return None
    except (TypeError, ValueError, KeyError, OverflowError):
        return None
    return {
        "revision": REVISION,
        "position_pool": list(pool),
        "total_draws": _TOTAL,
        "histogram": list(_HISTOGRAM),
        "ticket": {t: sum(_HISTOGRAM[t:]) / _TOTAL for t in range(1, 7)},
        "pool": {t: 1.0 for t in range(1, 7)},
        "optimal_thresholds": [4, 5],
        "ticket_count": 21,
        "played_numbers": 40,
    }


def improve_fixed_budget_540(ordered_pool, wheel, guarantee):
    """Replace only a recognized incumbent, preserving every exact tail count.

    The complete old signature ties its independently enumerated histogram
    to the actual input. A new constructor version cannot inherit that
    evidence. Classical guarantee coverage is checked from the real blocks.
    """
    try:
        g = _integer(guarantee)
        pool, rows = _positions(ordered_pool, wheel)
        digest, before = _PREVIOUS[g]
        if _sha(rows) != digest:
            return wheel, None
        if any(sum(_HISTOGRAM[t:]) < sum(before[t:]) for t in range(1, 7)):
            return wheel, None
        old_covered = {s for block in rows for s in combinations(block, g)}
        new_covered = {s for block in _BLOCKS for s in combinations(block, g)}
        if len(new_covered) < len(old_covered):
            return wheel, None
        candidate = [sorted(pool[p - 1] for p in block) for block in _BLOCKS]
        certificate = fixed_budget_540_certificate(pool, candidate)
        if certificate is None:
            return wheel, None
        coverage = _coverage_ratio_pct(len(new_covered), comb(40, g))
        return candidate, coverage
    except (TypeError, ValueError, KeyError, OverflowError):
        return wheel, None
