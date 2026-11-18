"""Eligibility of the last, exactly certified budget-wheel profile search.

Only generic 6/49 wheels are affected. The small bound keeps cold searches
short; fixed-size official LR candidates retain their ordered official rows.
The same predicate scopes the walk-forward and worker cache revision.
"""
from __future__ import annotations

from math import comb


def final_profile_applies(v, pick, guarantee, max_variants=0, condition=None,
                          draw_n=None, max_num=None, method="hitcover") -> bool:
    """Whether the external generator can run the final profile search."""
    try:
        v, pick, guarantee = int(v), int(pick), int(guarantee)
        cap = int(max_variants or 0)
        draws = pick if draw_n is None else int(draw_n)
        universe = int(max_num or 0)
        cond = guarantee if condition is None else int(condition)
    except (TypeError, ValueError, OverflowError):
        return False
    if not (
        str(method or "").strip().lower() == "hitcover"
        and pick == draws == 6 and universe == 49
        and 6 < v <= 12 and 3 <= guarantee <= pick
        and cond == guarantee and 2 <= cap <= 64
    ):
        return False
    from covering.designs import complete_design_size
    from covering.lr_schemes import lr_candidates

    full = complete_design_size(v, pick, guarantee) if guarantee < pick else comb(v, pick)
    if full is not None and full <= cap:
        return False
    return not lr_candidates(v, pick, cap)
