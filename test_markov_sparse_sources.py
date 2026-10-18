"""Markov source-row optimization preserves the dense reference exactly."""

import numpy as np
import pytest

from loto_enterprise.benchmark.methods_common import indicator, vector_to_scores
from loto_enterprise.benchmark.methods_relational import _markov
from loto_enterprise.core.ranking import rank_by_score


def _dense_reference(draws, max_num, lag):
    ind = indicator(draws, max_num)
    n, m = ind.shape
    if n <= lag:
        return vector_to_scores(np.zeros(m), max_num)
    src, dst = ind[:-lag], ind[lag:]
    counts = src.sum(axis=0)
    transition = src.T @ dst
    probabilities = transition / np.maximum(counts, 1.0)[:, None]
    return vector_to_scores(ind[n - lag] @ probabilities, max_num)


@pytest.mark.parametrize("max_num,draw_n", [(49, 6), (40, 6), (45, 5), (20, 1)])
@pytest.mark.parametrize("lag", [1, 2])
def test_markov_matches_dense_reference_at_every_prefix(max_num, draw_n, lag):
    rng = np.random.default_rng(20261018 + max_num)
    draws = np.array(
        [rng.choice(np.arange(1, max_num + 1), draw_n, replace=False)
         for _ in range(340)],
        dtype=np.int64,
    )
    original = draws.copy()
    draws.flags.writeable = False
    for length in range(len(draws) + 1):
        history = draws[:length]
        actual = _markov(history, max_num, lag)
        expected = _dense_reference(history, max_num, lag)
        assert actual == expected, (max_num, draw_n, lag, length)
        # Ties are part of the contract: every pool size retains membership
        # and the canonical ordering consumed by benchmark and production.
        assert rank_by_score(actual, max_num) == rank_by_score(expected, max_num)
    np.testing.assert_array_equal(draws, original)


@pytest.mark.parametrize("lag", [1, 2])
def test_markov_matches_dense_with_repeated_unseen_and_invalid_numbers(lag):
    # indicator() also supports direct calls with empty sources, duplicates,
    # and values outside the universe. Keep that behavior on strided input.
    draws = np.array(
        [[0, 0, 0], [1, 1, 8], [2, 3, 9], [-1, 0, 0], [3, 3, 2]] * 4
    )[::2]
    for length in range(len(draws) + 1):
        history = draws[:length]
        assert _markov(history, 7, lag) == _dense_reference(history, 7, lag)
