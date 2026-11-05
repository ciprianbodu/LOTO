"""Exact 5+/6 objectives for official LR rows under a declared weighted model.

The model is P(D) = product(w[n] for n in D) / e_d(w) for a fixed-size,
unordered draw of d distinct numbers. It is a research hypothesis, not an
established lottery bias or a predictor. Equal weights recover uniform odds.
Official rows and their ascending terminal mapping are never changed.

For six drawn numbers, five-hit events consist of a covered in-pool five-set
plus one outside number, or a covered in-pool six-set. These disjoint cases
are counted exactly. A four-packing of six-number rows (including Cod59)
has disjoint >=5-hit events, enabling the shorter sum over its actual rows.
Joker main-five events are distinct exact rows; an optional independent
second-urn probability gives 5+J. Six hits on a five-number row are impossible.
No payout category is inferred, especially no first-five category at 5/40.
"""

from __future__ import annotations

from functools import lru_cache
from itertools import combinations
from math import fsum, isfinite
from operator import index

import numpy as np

from covering.lr_schemes import LRScheme


_DRAW_COUNTS = {"6/49": 6, "5/40": 6, "joker": 5}


def _elementary_symmetric(values, degree):
    coefficients = [1.0] + [0.0] * degree
    for value in values:
        for k in range(degree, 0, -1):
            coefficients[k] += value * coefficients[k - 1]
    return coefficients[degree]


def _integer_label(value):
    if isinstance(value, bool):
        raise ValueError("Number labels must be integers, not booleans")
    try:
        return index(value)
    except TypeError as exc:
        raise ValueError("Number labels must be integers") from exc


@lru_cache(maxsize=16)
def _compile(n, rows, draw_n):
    if not rows or not 1 <= n <= 19 or draw_n not in (5, 6):
        raise ValueError("Expected a nonempty scheme on 1..19 positions and 5/6 draws")
    pick = len(rows[0])
    canonical = tuple(tuple(sorted(row)) for row in rows)
    if (
        pick not in (5, 6)
        or pick > draw_n
        or len(set(canonical)) != len(rows)
        or any(
            len(row) != pick or len(set(row)) != pick
            or any(isinstance(p, bool) or not isinstance(p, int) or not 1 <= p <= n
                   for p in row)
            for row in rows
        )
    ):
        raise ValueError("Expected distinct five/six-number positional rows")
    ticket_indices = np.asarray(canonical, dtype=np.intp) - 1
    four_packing = pick == 6 and len({
        subset for row in canonical for subset in combinations(row, 4)
    }) == len(rows) * 15
    shadow_five = shadow_six = None
    if draw_n == 6 and not four_packing:
        five_sets = {subset for row in canonical for subset in combinations(row, 5)}
        six_sets = {
            tuple(sorted((*subset, extra)))
            for subset in five_sets
            for extra in range(1, n + 1)
            if extra not in subset
        }
        shadow_five = np.asarray(sorted(five_sets), dtype=np.intp).reshape(-1, 5) - 1
        shadow_six = np.asarray(sorted(six_sets), dtype=np.intp).reshape(-1, 6) - 1
    complements = (
        np.asarray([
            [p for p in range(n) if p not in row] for row in ticket_indices
        ], dtype=np.intp).reshape(len(rows), n - pick)
        if four_packing else None
    )
    for array in (ticket_indices, shadow_five, shadow_six, complements):
        if array is not None:
            array.flags.writeable = False
    return pick, four_packing, ticket_indices, shadow_five, shadow_six, complements


class LRTailObjective:
    """Prepare one weighted draw model, then score many terminal-mapped pools.

    ``weights`` maps every number in the modeled universe to a finite,
    nonnegative weight. Its scale is irrelevant. At least draw_n weights must
    be positive. A smaller universe is allowed for independent brute-force
    verification. ``draw_n`` defaults to the Romanian game's actual draw size.
    Compilation of scheme geometry is shared; its masks never use the history.
    """

    def __init__(self, scheme: LRScheme, weights, *, draw_n=None, joker_probability=None):
        self.scheme = scheme
        self.draw_n = _integer_label(
            _DRAW_COUNTS.get(scheme.game) if draw_n is None else draw_n
        )
        self.geometry = _compile(scheme.n, scheme.variants, self.draw_n)
        normalized = {}
        for number, weight in weights.items():
            label = _integer_label(number)
            if isinstance(weight, bool):
                raise ValueError("Weights must be finite nonnegative numbers")
            try:
                value = float(weight)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError("Weights must be finite nonnegative numbers") from exc
            if not isfinite(value) or value < 0:
                raise ValueError("Weights must be finite nonnegative numbers")
            normalized[label] = value
        if len(normalized) < scheme.n or sum(w > 0 for w in normalized.values()) < self.draw_n:
            raise ValueError("Insufficient universe or positive weights for the draw")
        scale = max(normalized.values())
        self.weights = {n: w / scale for n, w in normalized.items()}
        self.total_weight = fsum(self.weights.values())
        self.denominator = _elementary_symmetric(self.weights.values(), self.draw_n)
        if not isfinite(self.denominator) or self.denominator <= 0:
            raise ValueError("Weighted draw normalizer is not numerically representable")
        if joker_probability is not None:
            if scheme.game != "joker":
                raise ValueError("A second-urn probability applies only to Joker")
            if isinstance(joker_probability, bool) or not isfinite(float(joker_probability)):
                raise ValueError("Joker probability must lie in [0,1]")
            joker_probability = float(joker_probability)
            if not 0 <= joker_probability <= 1:
                raise ValueError("Joker probability must lie in [0,1]")
        self.joker_probability = joker_probability

    def _pool_weights(self, pool):
        labels = [_integer_label(number) for number in pool]
        if len(labels) != self.scheme.n or len(set(labels)) != len(labels):
            raise ValueError("Pool must have exactly the scheme's distinct numbers")
        labels.sort()
        if any(n not in self.weights for n in labels):
            raise ValueError("Pool number missing from the modeled universe")
        chosen = set(labels)
        # Sum the complement directly: total-minus-selected loses tiny outside
        # mass when almost all weight lies on five numbers.
        outside = fsum(w for n, w in self.weights.items() if n not in chosen)
        return [self.weights[n] for n in labels], outside

    def evaluate_many(self, pools, *, batch_size=16):
        """Return an N-by-2 array of P(5+), P(6), in the input pool order.

        Batching bounds the temporary size for Cod48's 11,943 covered six-sets.
        Both columns concern main-number hits only. The optional Joker
        multiplier appears only in ``evaluate(pool)["p5_joker"]``.
        """
        batch_size = _integer_label(batch_size)
        if batch_size < 1:
            raise ValueError("batch_size must be a positive integer")
        pools = list(pools)
        result = np.empty((len(pools), 2), dtype=np.float64)
        pick, four_packing, tickets, fives, sixes, complements = self.geometry
        for start in range(0, len(pools), batch_size):
            batch = [self._pool_weights(pool) for pool in pools[start:start + batch_size]]
            pool_weights = np.asarray([row[0] for row in batch], dtype=np.float64)
            outside_pool = np.asarray([row[1] for row in batch], dtype=np.float64)
            ticket_weights = pool_weights[:, tickets]
            ticket_products = ticket_weights.prod(axis=2)
            six_numerator = ticket_products.sum(axis=1) if pick == 6 else 0.0
            if self.draw_n == 5:
                five_numerator = ticket_products.sum(axis=1)
            elif four_packing:
                e5 = np.zeros(ticket_products.shape, dtype=np.float64)
                for omitted in range(6):
                    e5 += np.delete(ticket_weights, omitted, axis=2).prod(axis=2)
                outside_tickets = (
                    outside_pool[:, None] + pool_weights[:, complements].sum(axis=2)
                )
                five_numerator = (outside_tickets * e5 + ticket_products).sum(axis=1)
            else:
                five_numerator = (
                    outside_pool * pool_weights[:, fives].prod(axis=2).sum(axis=1)
                    + pool_weights[:, sixes].prod(axis=2).sum(axis=1)
                )
            result[start:start + len(batch), 0] = five_numerator / self.denominator
            result[start:start + len(batch), 1] = six_numerator / self.denominator
        # Fail explicitly rather than let an invalid model score win a search.
        # Tiny roundoff at the unit boundary is clipped only after validation.
        if (
            not np.isfinite(result).all()
            or np.any(result < -1e-12)
            or np.any(result > 1 + 1e-12)
            or np.any(result[:, 1] > result[:, 0] + 1e-12)
        ):
            raise ArithmeticError("Invalid numerical tail probability")
        np.clip(result, 0.0, 1.0, out=result)
        return result

    def evaluate(self, pool):
        """Return p5plus, p6 (zero for five-number rows), and optional p5_joker."""
        p5, p6 = map(float, self.evaluate_many([pool], batch_size=1)[0])
        return {
            "p5plus": p5,
            "p6": p6,
            "p5_joker": None if self.joker_probability is None else p5 * self.joker_probability,
        }


def evaluate_tail(scheme, pool, weights, *, draw_n=None, joker_probability=None):
    """Convenience form; reuse LRTailObjective when evaluating many pools."""
    return LRTailObjective(
        scheme, weights, draw_n=draw_n, joker_probability=joker_probability
    ).evaluate(pool)
