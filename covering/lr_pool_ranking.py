"""Strict number ranking for Romanian main-urn LR pools."""

from collections.abc import Iterable
from operator import index

from loto_enterprise.core.ranking import (
    _max_addable, limit_consecutive_run, longest_consecutive_run, rank_by_score,
)

LR_MAX_CONSECUTIVE_RUN = 2
LR_RESTRICTED_RANGE = (5, 40)


def lr_pool_capacity(eligible: Iterable[int]) -> int:
    """Maximum number of eligible balls that fit without three consecutive balls."""
    return _max_addable(set(), set(map(index, eligible)), LR_MAX_CONSECUTIVE_RUN)


def select_ranked_lr_pool(ranked: Iterable[int], pool_size: int) -> list[int]:
    """Keep the best feasible ranks in order, with no relaxation of the run limit."""
    size = index(pool_size)
    if size < 0:
        raise ValueError("Dimensiunea pool-ului LR trebuie să fie nenegativă.")
    numbers = list(dict.fromkeys(map(index, ranked)))
    top = numbers[:size]
    if len(top) == size and longest_consecutive_run(top) <= LR_MAX_CONSECUTIVE_RUN:
        return top
    capacity = lr_pool_capacity(numbers)
    if capacity < size:
        raise ValueError(
            f"Se pot alege cel mult {capacity} din cele {len(numbers)} numere eligibile "
            f"fără trei consecutive. Pool-ul LR necesită {size}."
        )

    pool, applied, _ = limit_consecutive_run(numbers, size, LR_MAX_CONSECUTIVE_RUN)
    if applied != LR_MAX_CONSECUTIVE_RUN or len(pool) != size:
        raise RuntimeError("Selecția nu a produs un pool LR complet cu cel mult două numere consecutive.")
    return pool


def rank_lr_pool(scores: dict[int, float], pool_size: int) -> list[int]:
    """Apply the existing score/number tie break, then select the best feasible LR pool."""
    return select_ranked_lr_pool(rank_by_score(scores, len(scores)), pool_size)
