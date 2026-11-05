"""Semantic checks for the benchmark-only frozen interval/extrema candidate."""

from math import comb

import numpy as np
import pytest

from loto_enterprise.benchmark import methods_experimental as experimental
from loto_enterprise.core.ranking import rank_by_score
from loto_enterprise.core.score_validation import has_usable_score_variance


def _history(n=240, max_num=23, draw_n=6, seed=1703):
    rng = np.random.default_rng(seed)
    draws = np.argsort(rng.random((n, max_num)), axis=1)[:, :draw_n] + 1
    cuts = np.arange(n) // 2 * 2
    return draws, cuts


def _slow_reference(draws, max_num, cuts):
    """Direct lists/sets over every eligible past target, independent of batching."""
    n, draw_n = draws.shape
    intervals = [(low, low + width - 1) for width in range(max_num, 15, -1)
                 for low in range(1, max_num - width + 2)]
    total = comb(max_num, draw_n)
    choose = lambda size: comb(size, draw_n) if size >= draw_n else 0
    median_min = next(x for x in range(1, max_num + 1)
                      if 1 - choose(max_num - x) / total >= .5)
    median_max = next(x for x in range(1, max_num + 1)
                      if choose(x) / total >= .5)

    def state(cut):
        previous_day = draws[cuts[cut - 1]:cut]
        return int(np.mean([min(row) for row in previous_day]) <= median_min) + 2 * int(
            np.mean([max(row) for row in previous_day]) >= median_max
        )

    def frequencies(cut):
        past = draws[max(0, cut - 50):cut]
        return {x: int(np.count_nonzero(past == x)) for x in range(1, max_num + 1)}

    today_state = state(n)
    records = []
    for target in range(n):
        if target < n - 300 or cuts[target] < 200:
            continue
        freq = frequencies(cuts[target])
        order = sorted(freq, key=lambda x: (freq[x], x), reverse=True)
        drawn = set(draws[target])
        outcomes = [len(drawn & set([x for x in order if lo <= x <= hi][:16])) >= 4
                    for lo, hi in intervals]
        records.append((state(cuts[target]), outcomes))
    objectives = []
    for j in range(len(intervals)):
        recent = sum(row[j] for _, row in records)
        same = [row[j] for old_state, row in records if old_state == today_state]
        objectives.append((sum(same) + 50 * recent / len(records)) / (len(same) + 50))
    chosen = intervals[max(range(len(intervals)), key=lambda j: objectives[j])]
    freq = frequencies(n)
    order = sorted(freq, key=lambda x: (chosen[0] <= x <= chosen[1], freq[x], x), reverse=True)
    return chosen, freq, order


@pytest.mark.parametrize("max_num,draw_n,n", [(49, 6, 232), (40, 6, 528), (45, 5, 242)])
def test_matches_frozen_conditional_rule_with_double_days(max_num, draw_n, n):
    draws, cuts = _history(n, max_num, draw_n)
    interval, frequencies, expected = _slow_reference(draws, max_num, cuts)
    scores = experimental.score_interval_extrema_k16(draws, max_num, history_cutoffs=cuts)
    assert rank_by_score(scores, max_num) == expected
    assert all(interval[0] <= x <= interval[1] for x in rank_by_score(scores, 16))
    assert len(scores) == max_num and set(scores) == set(range(1, max_num + 1))
    assert all(0 <= score <= 1 for score in scores.values())
    for a in scores:
        for b in scores:
            same_class = (interval[0] <= a <= interval[1]) == (interval[0] <= b <= interval[1])
            if same_class and frequencies[a] == frequencies[b]:
                assert scores[a] == scores[b]


def test_internal_features_exclude_target_day_and_future(monkeypatch):
    draws, cuts = _history(260)
    original = draws.copy()
    rng = np.random.default_rng(884)
    changed = draws.copy()
    changed[200:] = np.argsort(rng.random((60, 23)), axis=1)[:, :6] + 1
    rank_calls, state_calls = [], []
    real_rank, real_state = experimental.rank_by_score, experimental._previous_state

    def record_rank(scores, k):
        rank_calls.append(scores.copy())
        return real_rank(scores, k)

    def record_state(*args):
        result = real_state(*args)
        state_calls.append((args[3], result))
        return result

    monkeypatch.setattr(experimental, "rank_by_score", record_rank)
    monkeypatch.setattr(experimental, "_previous_state", record_state)
    experimental.score_interval_extrema_k16(draws, 23, history_cutoffs=cuts)
    old_ranks, old_states = rank_calls[:2], state_calls[:2]
    rank_calls.clear()
    state_calls.clear()
    experimental.score_interval_extrema_k16(changed, 23, history_cutoffs=cuts)
    assert old_ranks == rank_calls[:2]  # Both targets 200 and 201 see prefix [:200].
    assert old_states == state_calls[:2] == [(200, old_states[0][1])] * 2
    assert np.array_equal(draws, original)


def test_none_or_empty_cutoffs_mean_one_draw_per_day():
    draws, _ = _history()
    fn = experimental.score_interval_extrema_k16
    expected = fn(draws, 23, history_cutoffs=np.arange(len(draws)))
    assert fn(draws, 23) == fn(draws, 23, history_cutoffs=[]) == expected


@pytest.mark.parametrize("bad", [
    [0, 0], [0, 1, 1.5, 3], [0, 1, np.nan, 3], [0, 1, np.inf, 3],
    [0, 1, -1, 3], [0, 1, 3, 3], [0, 1, 0, 3], [0, 0, 1, 3],
    [[0, 1, 2, 3]], [False, True, True, True],
])
def test_rejects_inconsistent_cutoffs_even_for_short_history(bad):
    draws, _ = _history(4)
    with pytest.raises(ValueError, match="history_cutoffs"):
        experimental.score_interval_extrema_k16(draws, 23, history_cutoffs=bad)


@pytest.mark.parametrize("bad", [
    [1, 2, 3, 4], [[1, 2, 3, 3]], [[1, 2, 3, 24]], [[0, 2, 3, 4]],
    [[1, 2, 3, 4.5]], [[1, 2, 3, np.nan]], [[1, 2, 3, np.inf]],
    [[True, False, True, False]], [[1, 2, 3, 4j]], [[]],
])
def test_rejects_invalid_draws_even_before_warmup(bad):
    with pytest.raises(ValueError, match="draws"):
        experimental.score_interval_extrema_k16(bad, 23)


@pytest.mark.parametrize("max_num", [0, -1, 23.5, np.nan, np.inf, True, "invalid"])
def test_rejects_invalid_universe(max_num):
    with pytest.raises(ValueError, match="max_num"):
        experimental.score_interval_extrema_k16([], max_num)


@pytest.mark.parametrize("n,max_num,draw_n", [(0, 49, 6), (200, 49, 6), (230, 15, 6), (230, 20, 3)])
def test_unsupported_or_insufficient_history_has_flat_scores(n, max_num, draw_n):
    draws, cuts = _history(n, max_num, draw_n)
    scores = experimental.score_interval_extrema_k16(draws, max_num, history_cutoffs=cuts)
    assert scores == {x: 0.0 for x in range(1, max_num + 1)}
    assert not has_usable_score_variance(scores)


def test_warmup_is_global_and_cannot_be_met_by_same_day_rows():
    draws, _ = _history(240)
    cuts = np.concatenate([np.arange(180), np.full(60, 180)])
    scores = experimental.score_interval_extrema_k16(draws, 23, history_cutoffs=cuts)
    assert not has_usable_score_variance(scores)


def test_empty_input_and_determinism_and_metadata():
    fn = experimental.score_interval_extrema_k16
    assert fn([], 49) == {x: 0.0 for x in range(1, 50)}
    draws, cuts = _history()
    before, before_cuts = draws.copy(), cuts.copy()
    assert fn(draws, 23, history_cutoffs=cuts) == fn(draws, 23, history_cutoffs=cuts)
    assert np.array_equal(draws, before) and np.array_equal(cuts, before_cuts)
    assert fn._uses_history_cutoffs is True
    entry = experimental.EXPERIMENTAL_METHODS["interval_extrema_k16"]
    assert entry[0] is fn and entry[1] == "experimental_interval"
