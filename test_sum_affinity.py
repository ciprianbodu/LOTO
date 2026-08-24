"""sum_affinity nu mai are voie să producă un pool consecutiv pe Joker.

Bug: formula veche era o gaussiană pe |k − mean_sum/draw_n|, independentă de
istoricul lui k. Pe Joker 5/45 vârful e ~23 → top-11 = 18–28 MEREU.

Fix: scorul e masa de extrageri cu SUMĂ tipică care conțin k.
"""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np

from loto_enterprise.benchmark.methods_classical import score_sum_affinity
from loto_enterprise.core.ranking import (
    is_consecutive_block,
    longest_consecutive_run,
    rank_by_score,
)
from loto_enterprise.core.pool_selection import select_pool_from_scores

JOKER_CSV = Path("_ISTORIC/joker.csv")
POOL_N = 11
MAX_NUM = 45


def _old_unimodal_sum_affinity(draws_2d, max_num: int) -> dict[int, float]:
    """Formula care producea 18–28 pe Joker — păstrată aici ca martor al bug-ului."""
    sums = np.array(
        [sum(int(v) for v in row if int(v) > 0) for row in draws_2d],
        dtype=np.float64,
    )
    mean_sum = float(sums.mean())
    std_sum = float(sums.std()) + 1e-9
    draw_n = draws_2d.shape[1]
    target = mean_sum / max(draw_n, 1)
    scores = {}
    for k in range(1, max_num + 1):
        scores[k] = float(
            np.exp(-((k - target) ** 2) / (2 * (std_sum / draw_n) ** 2 + 1e-9))
        )
    return scores


def _joker_draws() -> np.ndarray:
    assert JOKER_CSV.exists(), f"lipsește {JOKER_CSV}"
    rows = []
    with JOKER_CSV.open(newline="", encoding="utf-8") as f:
        for rec in csv.DictReader(f):
            rows.append([int(rec[c]) for c in ("n1", "n2", "n3", "n4", "n5")])
    assert rows, f"{JOKER_CSV} gol"
    return np.asarray(rows, dtype=np.int64)


def test_old_formula_is_consecutive_block_on_joker():
    """Martor: formula veche PE ACELEAȘI date dă un bloc consecutiv. Dacă testul
    ăsta cade, Joker-ul s-a schimbat atât de mult încât bug-ul nu s-ar mai vedea
    — atunci rescrie-l, nu șterge martorul."""
    draws = _joker_draws()
    pool = rank_by_score(_old_unimodal_sum_affinity(draws, MAX_NUM), POOL_N)
    assert is_consecutive_block(pool, min_size=POOL_N), sorted(pool)


def test_new_sum_affinity_joker_pool_is_not_consecutive():
    draws = _joker_draws()
    scores = score_sum_affinity(draws, MAX_NUM)
    assert scores, "scorer gol pe istoric Joker"
    assert all(np.isfinite(v) for v in scores.values())
    pool = rank_by_score(scores, POOL_N)
    assert len(pool) == POOL_N
    assert not is_consecutive_block(pool, min_size=6), (
        f"sum_affinity încă produce bloc consecutiv: {sorted(pool)}"
    )
    assert longest_consecutive_run(pool) < POOL_N


def test_new_sum_affinity_not_a_function_of_k_alone():
    """Pe date identice ca lungime dar cu numere diferite, top-k trebuie să se
    schimbe — formula veche depindea doar de mean/n, deci două istorice cu
    aceeași medie dădeau ACELAȘI ranking pe k."""
    rng = np.random.default_rng(0)
    a = rng.choice(np.arange(1, 46), size=(400, 5), replace=True)
    # forțează unice pe rând
    for i, row in enumerate(a):
        a[i] = rng.choice(np.arange(1, 46), size=5, replace=False)
    b = a.copy()
    b[:, :] = 46 - a  # oglindă 1↔45 — sumele pe rând NU sunt identice, dar
    scores_a = score_sum_affinity(a, MAX_NUM)
    scores_b = score_sum_affinity(b, MAX_NUM)
    pool_a = set(rank_by_score(scores_a, POOL_N))
    pool_b = set(rank_by_score(scores_b, POOL_N))
    assert pool_a != pool_b


def test_select_pool_flags_consecutive_block():
    # scoruri unimodale artificiale → pool 18–28
    scores = {k: float(np.exp(-((k - 23) ** 2) / 8.0)) for k in range(1, 46)}
    audit: dict = {}
    pool = select_pool_from_scores(scores, POOL_N, blacklist=set(), audit=audit, max_num=45)
    assert is_consecutive_block(pool, min_size=6)
    assert audit.get("pool_is_consecutive_block") is True
    assert "18" in audit.get("pool_consecutive_warning", "") or "18" in str(sorted(pool))


def test_longest_consecutive_run_helpers():
    assert longest_consecutive_run([]) == 0
    assert longest_consecutive_run([7]) == 1
    assert longest_consecutive_run([18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28]) == 11
    assert longest_consecutive_run([1, 2, 3, 10, 11]) == 3
    assert is_consecutive_block([18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28])
    assert not is_consecutive_block([8, 9, 12, 17, 19, 32, 35, 38, 39, 41, 44])
