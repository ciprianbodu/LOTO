"""Behavioral contracts across all scorers and all four lottery geometries."""

from __future__ import annotations

import numpy as np
import pytest

from loto_enterprise.benchmark.methods import METHODS
from loto_enterprise.benchmark.methods_common import indicator
from loto_enterprise.benchmark.methods_learning import _features_from, _train_rows
from loto_enterprise.benchmark.runner import GameDef, _evaluate_fold
from loto_enterprise.core.score_validation import has_usable_score_variance

GEOMETRIES = [(49, 6), (40, 5), (45, 5), (20, 1)]
PAIR_METHODS = [
    "cooc_last3",
    "anti_cooc_last",
    "pair_lift_last",
    "pagerank_cooc",
    "pair_transition",
    "rwr_last_draw",
    "hawkes_cross",
]


def history(max_num, draw_n, length, seed=391):
    rng = np.random.default_rng(seed)
    return np.array(
        [
            rng.choice(np.arange(1, max_num + 1), draw_n, replace=False)
            for _ in range(length)
        ],
        dtype=np.int64,
    ).reshape(length, draw_n)


@pytest.mark.parametrize("name", sorted(METHODS))
@pytest.mark.parametrize("max_num,draw_n", GEOMETRIES)
def test_scorer_contract_and_interleaved_determinism(name, max_num, draw_n):
    scorer = METHODS[name][0]
    other = history(max_num, draw_n, 83, seed=992)
    # Cold starts, model activation and both sides of the learning window.
    for length in (0, 1, 5, 29, 30, 80, 401, 700):
        draws = history(max_num, draw_n, length)
        original = draws.copy()
        draws.setflags(write=False)
        scores = scorer(draws, max_num)
        assert set(scores) == set(range(1, max_num + 1)), (name, length)
        values = np.array(list(scores.values()), dtype=float)
        assert np.isfinite(values).all(), (name, length)
        assert ((values >= 0) & (values <= 1)).all(), (name, length)
        scorer(other, max_num)
        assert scorer(draws, max_num) == scores, (name, length)
        np.testing.assert_array_equal(draws, original)


@pytest.mark.parametrize("name", sorted(set(METHODS) - {"random"}))
@pytest.mark.parametrize("max_num,draw_n", GEOMETRIES[:3])
def test_number_order_within_draw_does_not_change_scores(name, max_num, draw_n):
    draws = history(max_num, draw_n, 180)
    scorer = METHODS[name][0]
    # The random reference intentionally hashes the raw history representation.
    assert scorer(draws, max_num) == scorer(draws[:, ::-1], max_num)


@pytest.mark.parametrize("name", PAIR_METHODS)
def test_single_ball_pair_methods_are_flat_and_not_evaluated(name):
    draws = history(20, 1, 100)
    assert not has_usable_score_variance(METHODS[name][0](draws, 20))
    game = GameDef(
        "joker_urna2",
        "Joker Urna 2",
        "unused.csv",
        ["joker"],
        20,
        1,
        pool_extra=0,
        is_single_pick=True,
    )
    fold, _ = _evaluate_fold(name, draws[:97], draws[97:], game, block_size=1)
    assert fold.failed is True
    assert fold.n_eval == 0
    assert "unusable scores" in fold.error


@pytest.mark.parametrize("max_num,draw_n", GEOMETRIES)
def test_learning_features_exclude_target_and_future(max_num, draw_n):
    draws = history(max_num, draw_n, 460)
    ind = indicator(draws, max_num)
    p0 = draw_n / max_num
    all_features = _features_from(ind, p0, 0)
    for target in (0, 1, 29, 50, 301, 459):
        altered = ind.copy()
        altered[target:] = 1 - altered[target:]
        np.testing.assert_array_equal(
            all_features[target], _features_from(altered, p0, target)[0]
        )
        np.testing.assert_array_equal(
            all_features[target], _features_from(ind[:target], p0, target)[-1]
        )
    x, y, next_x = _train_rows(ind, p0)
    # 460 observations leave a training window of 400; labels must align
    # with the features available strictly before the corresponding draw.
    np.testing.assert_array_equal(x.reshape(400, max_num, 6), all_features[60:460])
    np.testing.assert_array_equal(y.reshape(400, max_num), ind[60:460])
    np.testing.assert_array_equal(next_x, all_features[460])
