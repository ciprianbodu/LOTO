"""The research screen must preserve production pools and temporal splits."""

import importlib.util
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "pool_selection_screen_20261019",
    ROOT / "scripts/analysis/pool_selection_649_2026_10_19.py",
)
screen = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(screen)


def test_same_day_draws_cannot_cross_development_confirmation_boundary():
    dates = np.repeat(np.array([f"2026-01-{i:02}" for i in range(1, 11)]), 2)
    labels = screen.split_labels(dates, np.arange(len(dates)))
    assert np.all(labels[:12] == "train")
    assert np.all(labels[12:16] == "development")
    assert np.all(labels[16:] == "confirmation")
    for day in np.unique(dates):
        assert len(set(labels[dates == day])) == 1


def test_research_pools_match_engine_with_ties_and_consecutive_limit():
    from loto_enterprise.core.pool_selection import select_pool_from_scores
    from loto_enterprise.core.ranking import longest_consecutive_run

    # Tied scores exercise number-desc tie-breaking. Long runs force the option.
    scores = np.tile(np.arange(49, dtype=float) // 3, (2, 1))
    draws = np.array([[44, 45, 46, 47, 48, 49], [1, 4, 9, 16, 25, 36]])
    hits, pools, ties = screen.score_hits(scores, draws)
    assert ties.any()
    for t, row in enumerate(scores):
        mapping = dict(enumerate(row, start=1))
        for j, k in enumerate(screen.KS):
            for mode, limit in enumerate((0, 2)):
                expected = select_pool_from_scores(
                    mapping, k, set(), max_consecutive_run=limit
                )
                actual = sorted(pools[t, mode, j, :k])
                assert actual == expected
                assert hits[t, mode, j] == len(set(actual) & set(draws[t]))
                if limit:
                    assert longest_consecutive_run(actual) <= limit
    assert not np.array_equal(pools[:, 0], pools[:, 1])


@pytest.mark.parametrize("value", [0.0, float("nan"), float("inf")])
def test_unusable_candidate_cannot_silently_become_a_ranked_winner(value):
    with pytest.raises(ValueError, match="unusable scores"):
        screen.score_hits(np.full((1, 49), value), np.array([[1, 2, 3, 4, 5, 6]]))


def test_primary_metric_counts_draws_and_pool_sizes_without_creating_new_draws():
    hits = np.array([[4] * 8, [0] * 8, [3] * 8])
    result = screen.metrics(hits)
    assert result["n_draws"] == 3
    assert result["primary"] == pytest.approx(1 / 3)
    assert result["rate3plus"] == pytest.approx(2 / 3)
    assert result["zero_rate"] == pytest.approx(1 / 3)
    assert all(v["events4"] == 1 for v in result["per_k"].values())
