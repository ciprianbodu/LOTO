"""Wheel method dispatcher."""
from __future__ import annotations

from budget_cover import wheel_maxcover
from covering.common import _coverage_pct, _greedy_fallback, ensure_pool_numbers_on_tickets
from covering.designs import wheel_lajolla, wheel_lotto, wheel_union34
from covering.ilp import wheel_ilp
from covering.search import wheel_annealing, wheel_genetic

WHEEL_METHODS = {
    "maxcover": wheel_maxcover,
    "ilp": wheel_ilp,
    "annealing": wheel_annealing,
    "genetic": wheel_genetic,
    "lajolla": wheel_lajolla,
    "union34": wheel_union34,
}


def generate_wheel(
    method: str,
    pool,
    pick,
    guarantee,
    max_variants=0,
    scores=None,
    condition: int | None = None,
):
    """Selectează algoritmul de wheeling. 'greedy' (sau necunoscut) → canonic.

    `condition` (numărul de numere din pool care trebuie să cadă ca garanția să
    se aplice) > `guarantee` comută pe lotto design „t dacă p" (`wheel_lotto`),
    indiferent de `method`: designurile locale și coverele clasice există doar
    pentru p == t. `condition` None sau egal cu garanția = comportamentul vechi.
    """
    if int(guarantee) > int(pick):
        # Pipeline-ul clampeaza deja; API-ul direct arunca altfel
        # `ValueError: r must be non-negative` din itertools, fara context.
        raise ValueError(
            f"guarantee={guarantee} > pick={pick}: garanția nu poate depăși numerele extrase"
        )
    if condition is not None and int(condition) != int(guarantee):
        if int(condition) < int(guarantee):
            raise ValueError(f"condition={condition} < guarantee={guarantee}")
        return wheel_lotto(pool, pick, guarantee, int(condition), max_variants, scores)
    fn = WHEEL_METHODS.get((method or "greedy").strip().lower())
    if fn is None:
        wheel, cov = _greedy_fallback(pool, pick, guarantee, max_variants, scores)
    else:
        wheel, cov = fn(pool, pick, guarantee, max_variants, scores)
    if int(max_variants or 0) > 0:
        wheel = ensure_pool_numbers_on_tickets(wheel, pool, pick)
        cov = _coverage_pct(wheel, pool, guarantee)
    return wheel, cov
