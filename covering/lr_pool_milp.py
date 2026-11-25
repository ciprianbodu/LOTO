"""Bounded maximum-coverage MILP for complete LR zones."""

from collections import defaultdict
from math import ceil, isfinite

import numpy as np


def _classes(events, layers):
    """Aggregate draw outcomes covered by exactly the same placement indices."""
    count = max(mask.bit_length() for mask, _ in layers)
    width = (count + 7) // 8
    packed = np.frombuffer(
        b"".join(events_for_zone.to_bytes(width, "little") for events_for_zone in events),
        dtype=np.uint8,
    ).reshape(len(events), width)
    choices = np.unpackbits(packed, axis=1, count=count, bitorder="little")
    signatures = np.ascontiguousarray(np.packbits(choices.T, axis=1, bitorder="little"))
    weights = np.zeros(count, dtype=np.int64)
    for layer, weight in layers:
        bits = np.frombuffer(layer.to_bytes(width, "little"), dtype=np.uint8)
        weights[np.unpackbits(bits, count=count, bitorder="little").astype(bool)] = weight
    unique, inverse = np.unique(signatures, axis=0, return_inverse=True)
    totals = np.zeros(len(unique), dtype=np.int64)
    np.add.at(totals, inverse.reshape(-1), weights)
    signatures = np.unpackbits(unique, axis=1, count=len(events), bitorder="little")
    return {
        tuple(indices): int(weight)
        for signature, weight in zip(signatures, totals, strict=True)
        if weight and (indices := np.flatnonzero(signature)).size
    }


def solve_zone_coverage(variants, unit, fee, per_ticket, budget, event_groups, protection, layers, stop, seconds):
    """Return selected option indices and a solver bound for these options.

    Each event group has one or more draw-event layers, one per eligible Joker
    plus an aggregated weight for Joker outcomes no selected zone can match.
    Binary coverage is constrained by the OR over all complete zones.
    """
    from scipy.optimize import Bounds, LinearConstraint, milp
    from scipy.sparse import coo_matrix

    count = len(variants)
    maximum_tickets = (budget // unit + per_ticket - 1) // per_ticket
    rows, columns, coefficients = [], [], []
    lower, upper = [], []
    objective = [0] * (count + 1)
    ub = [1] * count + [maximum_tickets]

    def constraint(entries, low=-np.inf, high=np.inf):
        row = len(lower)
        for name, coefficient in entries:
            rows.append(row)
            columns.append(name)
            coefficients.append(coefficient)
        lower.append(low)
        upper.append(high)

    constraint(
        [(i, v * unit) for i, v in enumerate(variants)] + [(count, fee)],
        high=budget,
    )
    constraint([(i, 1) for i in range(count)] + [(count, -per_ticket)], high=0)
    for group_index, group in enumerate(event_groups):
        classes = defaultdict(int)
        for events, multiplicity in group:
            stop()
            for signature, weight in _classes(events, layers).items():
                classes[signature] += multiplicity * weight
        terms = []
        for signature, weight in sorted(classes.items()):
            i = len(objective)
            objective.append(-weight if group_index == 0 else 0)
            ub.append(1)
            constraint([(i, 1), *((int(j), -1) for j in signature)], high=0)
            terms.append((i, weight))
        if group_index:
            constraint(terms, low=protection[group_index - 1])

    stop()
    time_left = seconds()
    if time_left <= 0.02:
        return (), None, False
    matrix = coo_matrix(
        (np.asarray(coefficients, dtype=float), (rows, columns)),
        shape=(len(lower), len(objective)),
    ).tocsc()
    # With binary zone variables, continuous coverage variables attain the
    # correct 0/1 OR maximum and correctly impose the protected lower bounds.
    integrality = np.zeros(len(objective), dtype=np.int32)
    integrality[:count + 1] = 1
    # Keep the scheduler shared with other application users of HiGHS.
    # A different thread count would be refused after a prior SciPy solve.
    result = milp(
        np.array(objective, dtype=float),
        integrality=integrality,
        bounds=Bounds(np.zeros(len(ub)), ub),
        constraints=LinearConstraint(matrix, lower, upper),
        options={"time_limit": time_left, "mip_rel_gap": 0.0},
    )
    bound = getattr(result, "mip_dual_bound", None)
    upper_bound = None
    if bound is not None and isfinite(bound):
        upper_bound = max(0, ceil(-bound))
        if result.status == 0 and abs(bound - round(bound)) <= 1e-6:
            upper_bound = max(0, round(-bound))
    if result.x is None:
        return (), upper_bound, False
    selected = tuple(int(i) for i in np.flatnonzero(result.x[:count] > 0.5))
    return selected, upper_bound, result.status == 0
