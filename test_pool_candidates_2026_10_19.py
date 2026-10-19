"""Causality and production-pool parity of the four research aggregators."""

from __future__ import annotations

import numpy as np
import pytest

from scripts.analysis import pool_candidates_2026_10_19 as candidates
from loto_enterprise.core.pool_selection import select_pool_from_scores


def _fixture(rows=14):
    rng = np.random.default_rng(20261019)
    scores = rng.normal(size=(rows, 3, 49))
    draws = np.stack(
        [rng.choice(np.arange(1, 50), 6, replace=False) for _ in range(rows)]
    )
    groups = np.arange(rows) // 2
    actuals = candidates._actuals(draws, rows)
    freq = (
        candidates._prefix_frequency(actuals, candidates._group_slices(groups, rows))
        + np.arange(49) / 10
    )
    names = ("frequency", "markov_lag2", "markov_pairs")
    return scores, draws, names, groups, freq


def test_four_fixed_candidates_are_finite_deterministic_and_do_not_mutate_inputs():
    scores, draws, names, groups, freq = _fixture()
    originals = [x.copy() for x in (scores, draws, groups, freq)]
    first = candidates.candidate_scores(
        scores, draws, names, groups=groups, frequency_past=freq
    )
    second = candidates.candidate_scores(
        scores, draws, names, groups=groups, frequency_past=freq
    )
    assert tuple(first) == candidates.CANDIDATE_NAMES
    for name, output in first.items():
        assert output.shape == (len(scores), 49)
        assert np.isfinite(output).all()
        assert (output >= 0).all() and (output <= 1 + 1e-14).all()
        np.testing.assert_array_equal(output, second[name])
    for current, original in zip((scores, draws, groups, freq), originals):
        np.testing.assert_array_equal(current, original)


@pytest.mark.parametrize("stop", [1, 5, 6, 13])
def test_every_prefix_matches_full_run_even_when_cut_inside_same_day(stop):
    scores, draws, names, groups, freq = _fixture()
    full = candidates.candidate_scores(
        scores, draws, names, groups=groups, frequency_past=freq
    )
    prefix = candidates.candidate_scores(
        scores[:stop],
        draws[:stop],
        names,
        groups=groups[:stop],
        frequency_past=freq[:stop],
    )
    for name in full:
        np.testing.assert_array_equal(full[name][:stop], prefix[name])


def test_changing_current_and_future_targets_cannot_change_any_current_group_output():
    scores, draws, names, groups, freq = _fixture()
    changed = draws.copy()
    changed[5:] = np.stack(
        [np.roll(np.arange(1, 50), i)[:6] for i in range(len(draws) - 5)]
    )
    before = candidates.candidate_scores(
        scores, draws, names, groups=groups, frequency_past=freq
    )
    after = candidates.candidate_scores(
        scores, changed, names, groups=groups, frequency_past=freq
    )
    # Rows 4 and 5 share a group; both are emitted before target row 5 is seen.
    for name in before:
        np.testing.assert_array_equal(before[name][:6], after[name][:6])


def test_same_day_outcome_order_does_not_change_later_predictions():
    scores, draws, names, groups, freq = _fixture()
    order = np.arange(len(scores))
    order[4:6] = [5, 4]
    before = candidates.candidate_scores(
        scores, draws, names, groups=groups, frequency_past=freq
    )
    after = candidates.candidate_scores(
        scores[order], draws[order], names, groups=groups, frequency_past=freq[order]
    )
    for name in before:
        np.testing.assert_array_equal(before[name][order], after[name])


def test_indicator_and_ball_label_contracts_are_equivalent_and_generator_matches():
    scores, draws, names, groups, freq = _fixture()
    labels = candidates.candidate_scores(
        scores, draws, names, groups=groups, frequency_past=freq
    )
    indicator = candidates._actuals(draws, len(draws))
    adapted = list(candidates.generate(scores, indicator, freq, names, groups=groups))
    assert len(adapted) == 4
    for name, family, output in adapted:
        assert family == candidates.FAMILY
        np.testing.assert_array_equal(labels[name], output)


def test_mean_rank_ties_have_identical_features_without_label_jitter():
    values = np.r_[np.zeros(20), np.ones(29)][None, :]
    ranks = candidates._mean_ranks(values)[0]
    assert np.all(ranks[:20] == ranks[0])
    assert np.all(ranks[20:] == ranks[-1])
    assert ranks[0] == pytest.approx(9.5 / 48)
    assert ranks[-1] == pytest.approx(34 / 48)


@pytest.mark.parametrize("kind", ["random", "tied"])
def test_reward_and_vote_pools_match_production_consecutive_limit(kind):
    scores, draws, _names, _groups, _freq = _fixture(rows=2)
    score = scores[0]
    if kind == "tied":
        score = np.round(score)
    _rank, votes, masks = candidates._features(score)
    target = candidates._actuals(draws, len(draws))[0]
    observed = np.einsum("mkn,n->mk", masks, target, dtype=np.int64)
    reference = []
    for expert, row in enumerate(score):
        counts = []
        for index, size in enumerate(candidates.POOL_SIZES):
            pool = select_pool_from_scores(
                dict(enumerate(row, start=1)), size, set(), max_consecutive_run=2
            )
            assert set(pool) == set(np.flatnonzero(masks[expert, index]) + 1)
            counts.append(len(set(pool) & set(draws[0])))
        reference.append(counts)
    np.testing.assert_array_equal(observed, reference)
    np.testing.assert_array_equal(votes, masks.mean(axis=1))


def test_online_reward_learns_only_after_emitting_the_observation():
    rows = 12
    target = np.array([1, 4, 7, 10, 13, 16])
    strong = np.arange(49, dtype=float) / 100
    strong[target - 1] = 10
    weak = np.arange(49, dtype=float)
    weak[target - 1] = -100
    scores = np.tile(np.stack((weak, strong)), (rows, 1, 1))
    draws = np.tile(target, (rows, 1))
    out = candidates.candidate_scores(scores, draws, ("markov_lag2", "frequency"))
    for name in out:
        assert out[name][1, target - 1].mean() > out[name][0, target - 1].mean()
        assert out[name][-1, target - 1].mean() > out[name][1, target - 1].mean()


def test_flat_and_nonfinite_experts_use_only_supplied_prefix_frequency():
    scores, draws, names, groups, freq = _fixture()
    scores[:, 0] = 0
    scores[:, 1, 0] = np.nan
    scores[:, 2, 0] = np.inf
    dirty = candidates.candidate_scores(
        scores, draws, names, groups=groups, frequency_past=freq
    )
    repaired = candidates.candidate_scores(
        np.broadcast_to(freq[:, None], scores.shape),
        draws,
        names,
        groups=groups,
        frequency_past=freq,
    )
    for name in dirty:
        np.testing.assert_array_equal(dirty[name], repaired[name])


def test_optional_prefix_frequency_is_strictly_group_past():
    _scores, draws, _names, groups, _freq = _fixture()
    actual = candidates._actuals(draws, len(draws))
    freq = candidates._prefix_frequency(
        actual, candidates._group_slices(groups, len(draws))
    )
    assert not freq[0].any() and not freq[1].any()
    np.testing.assert_array_equal(freq[2], actual[:2].sum(axis=0))
    np.testing.assert_array_equal(freq[3], freq[2])


@pytest.mark.parametrize("groups", [[0, 1, 0], [0, np.nan, 2]])
def test_noncontiguous_or_missing_groups_are_rejected(groups):
    scores, draws, names, _groups, freq = _fixture(rows=3)
    with pytest.raises(ValueError, match="Group labels"):
        candidates.candidate_scores(
            scores, draws, names, groups=groups, frequency_past=freq
        )


def test_duplicate_or_missing_anchor_names_are_rejected():
    scores, draws, _names, groups, freq = _fixture()
    for names in (
        ("frequency", "markov_pairs", "ewma"),
        ("markov_lag2", "markov_lag2", "frequency"),
    ):
        with pytest.raises(ValueError, match="expert names"):
            candidates.candidate_scores(
                scores, draws, names, groups=groups, frequency_past=freq
            )


def test_invalid_draw_geometry_or_fractional_ball_is_rejected():
    scores, draws, names, groups, freq = _fixture()
    draws = draws.astype(float)
    draws[0, 0] = 1.5
    with pytest.raises(ValueError, match="integers"):
        candidates.candidate_scores(
            scores, draws, names, groups=groups, frequency_past=freq
        )


def test_empty_input_returns_four_empty_score_matrices():
    out = candidates.candidate_scores(
        np.empty((0, 1, 49)), np.empty((0, 6)), ("markov_lag2",)
    )
    assert len(out) == 4 and all(array.shape == (0, 49) for array in out.values())
