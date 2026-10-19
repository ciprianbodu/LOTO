"""Parity of all8 research pools with separate canonical production calls."""

import importlib.util
from pathlib import Path

import numpy as np
import pytest

SPEC = importlib.util.spec_from_file_location(
    "pool_combination_eval",
    Path(__file__).resolve().parent
    / "scripts/analysis/pool_combination_eval_2026_10_19.py",
)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_constrained16_prefixes_match_independent_calls_for_all_sizes():
    from loto_enterprise.core.pool_selection import apply_consecutive_limit
    from loto_enterprise.core.ranking import rank_by_score

    rng = np.random.default_rng(2026101902)
    patterns = [
        np.arange(49),
        -np.arange(49),
        np.arange(49) // 4,
        np.r_[np.arange(17) + 1, np.zeros(32)],
        np.tile([0, 1, 2, 3, 4, 5, 6], 7),
    ]
    patterns += [rng.permutation(49) for _ in range(600)]
    patterns += [rng.integers(0, 4, 49) for _ in range(200)]
    scores = np.asarray(patterns, dtype=float)
    draws = np.tile([1, 2, 3, 24, 48, 49], (len(scores), 1))
    hits, _ = module.evaluate_scores(scores, draws)
    for t, row in enumerate(scores):
        ranked = rank_by_score(dict(enumerate(row, start=1)), 49)
        actual = set(draws[t])
        for j, k in enumerate(range(9, 17)):
            for mode, limit in enumerate((0, 2)):
                chosen = apply_consecutive_limit(ranked, k, limit)
                assert hits[t, mode, j] == len(actual.intersection(chosen))
                if mode:
                    assert chosen == apply_consecutive_limit(ranked, 16, 2)[:k]


@pytest.mark.parametrize(
    "bad", [np.zeros((1, 49)), np.full((1, 49), np.nan), np.full((1, 49), np.inf)]
)
def test_unusable_consensus_is_never_implicitly_ranked(bad):
    with pytest.raises(ValueError, match="unusable"):
        module.evaluate_scores(bad, np.array([[1, 2, 3, 4, 5, 6]]))


def test_incomplete_universe_rejected_before_prefix_optimization():
    with pytest.raises(ValueError, match="49"):
        module.evaluate_scores(np.ones((1, 48)), np.array([[1, 2, 3, 4, 5, 6]]))


def test_duplicate_target_balls_rejected():
    with pytest.raises(ValueError, match="distinct"):
        module.evaluate_scores(np.arange(49)[None, :], np.array([[1, 2, 3, 4, 5, 5]]))
