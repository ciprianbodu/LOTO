"""Shared validation for scorer outputs used by bench and production."""

from __future__ import annotations

import math
from collections.abc import Mapping


SCORE_VARIANCE_EPSILON = 1e-12


def has_usable_score_variance(raw: Mapping[int, object] | None) -> bool:
    """Return whether scores are finite and contain ranking information.

    Empty, single-value, flat, non-numeric and non-finite outputs cannot define
    a reliable ranking. Keeping this rule here prevents benchmark/production
    drift when a scorer fails silently by returning zeroes or NaN values.
    """
    if not raw:
        return False
    try:
        values = [float(value) for value in raw.values()]
    except (TypeError, ValueError, OverflowError):
        return False
    if len(values) < 2 or not all(math.isfinite(value) for value in values):
        return False
    return (max(values) - min(values)) > SCORE_VARIANCE_EPSILON
