"""Fixed excluded numbers apply to both walk-forward evaluation and the live pool."""

import numpy as np
import pytest

from covering.lr_selection import resolve_lr_scheme
from loto_engine import LotoEngine
from loto_enterprise.core import dynamic_winner as dynamic
from loto_enterprise.core.dynamic_pools import ranked_pools
from loto_enterprise.core.pool_selection import select_pool_from_scores
from loto_enterprise.core.ranking import rank_by_score


SCHEME = resolve_lr_scheme("6/49", 57)
EXCLUDED = tuple(range(1, 13))


def test_candidates_control_and_live_use_the_same_complement(monkeypatch):
    count = dynamic.WARMUP_DRAWS + dynamic.WINDOW_DAYS
    positions = np.asarray(SCHEME.variants[0]) - 1
    # Every fourth draw comes from random's second choice in the ranking.
    matrix = np.stack([positions + (25 if i % 4 == 0 else 13) for i in range(count)])
    cutoffs = np.arange(count)
    scores = np.zeros((len(dynamic.CANDIDATES) + 1, 49))
    scores[:, :12] = 100
    scores[:, 36:48] = 10
    markov = dynamic.CANDIDATES.index("markov_lag2")
    scores[markov, 12:24] = 20
    scores[-1, 24:36] = 20

    def score_rows(candidates, history, bounds, universe, reported):
        assert len(history) == len(bounds) and universe == 49
        return scores, np.zeros(len(candidates), dtype=bool)

    monkeypatch.setattr(dynamic, "_score_rows", score_rows)
    normal = dynamic.choose_dynamic_winner(matrix, cutoffs, SCHEME)
    assert normal == dynamic.choose_dynamic_winner(matrix, cutoffs, SCHEME, excluded_numbers=[])
    assert normal[1]["method"] == "frequency"
    assert normal[1]["hits"]["4"] == normal[1]["random_hits"]["4"] == 0

    batch = dynamic.dynamic_forecasts(
        matrix, cutoffs, SCHEME, [count - 1, count], excluded_numbers=EXCLUDED,
    )
    for origin, result in batch.items():
        assert result == dynamic.choose_dynamic_winner(
            matrix[:origin], cutoffs[:origin], SCHEME, excluded_numbers=list(EXCLUDED),
        )
        scores_live, report = result
        assert report["method"] == "markov_lag2"
        assert set(scores_live) == set(range(13, 50))
        assert set(rank_by_score(scores_live, 12)) == set(range(13, 25))
        assert report["excluded_numbers"] == list(EXCLUDED)
        assert report["eligible_count"] == 37
        days = list(range(dynamic.WARMUP_DRAWS, origin))
        controls = sum(i % 4 == 0 for i in days)
        assert report["hits"]["4"] == len(days) - controls
        assert report["random_hits"]["4"] == controls


@pytest.mark.parametrize("excluded", [(True,), (2, 2), (3.5,), (0,), (50,), tuple(range(1, 39))])
def test_rejects_invalid_exclusions_even_for_short_history(excluded):
    history = np.array([[1, 2, 3, 4, 5, 6]])
    with pytest.raises(ValueError):
        dynamic.choose_dynamic_winner(history, [0], SCHEME, excluded_numbers=excluded)


def test_restricted_interval_and_short_history_frequency_scores():
    history = np.array([[1, 2, 3, 4, 5, 6]])
    scores, report = dynamic.choose_dynamic_winner(
        history, [0], SCHEME, restrict_min=15, restrict_max=29, excluded_numbers=[15, 17, 19],
    )
    assert sorted(scores) == [16, 18, *range(20, 30)]
    assert report["reason"] == "insufficient_history"
    with pytest.raises(ValueError, match="11"):
        dynamic.choose_dynamic_winner(
            history, [0], SCHEME, restrict_min=15, restrict_max=29, excluded_numbers=[15, 17, 19, 20],
        )


def test_consecutive_limit_and_penalty_keep_original_ball_labels():
    eligible = (2, 4, 5, 7, 9, 10, 11, 12, 16, 18, 19, 20, 25, 31, 32, 38)
    history = np.array([[2, 4, 20, 31, 32, 38]])
    raw = np.stack([np.arange(49) % 7, np.arange(49)[::-1]])
    pools = ranked_pools(
        raw, pool_size=12, universe=49, eligible_numbers=eligible, max_run=2,
        past_draws=history, penalty_draws=1, penalty_factor=0,
    )
    for row, pool in zip(raw, pools, strict=True):
        sparse = {n: row[n - 1] for n in eligible}
        adjusted, _ = LotoEngine.apply_recent_penalty(sparse, history, 1, 0, 49)
        assert pool.tolist() == select_pool_from_scores(adjusted, 12, set(), max_consecutive_run=2)
