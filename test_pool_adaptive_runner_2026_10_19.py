"""Mean-rank blends preserve ties, labels and the canonical fallback."""

import numpy as np
import pytest
from scipy.stats import rankdata

from scripts.analysis import pool_adaptive_649_2026_10_19 as study


def test_reverse_ranks_fall_back_to_exact_frequency_and_are_flagged():
    left = np.arange(49, dtype=float)[None, :]
    frequency = np.arange(49, dtype=float)[None, :] ** 2
    scores, flat = study.blend_scores(left, -left, frequency)
    np.testing.assert_array_equal(scores, frequency)
    np.testing.assert_array_equal(flat, [True])


def test_partial_ties_keep_mean_rank_and_relabel_equivariance():
    left = np.array([[0, 0, 1, 2, 2, 3, 3] * 7], dtype=float)
    right = np.roll(left, 3, axis=1)
    frequency = np.arange(49, dtype=float)[None, :]
    original = [v.copy() for v in (left, right, frequency)]
    score, flat = study.blend_scores(left, right, frequency)
    expected = (
        rankdata(left, method="average", axis=1)
        + rankdata(right, method="average", axis=1)
        - 2
    ) / 96
    np.testing.assert_array_equal(score, expected)
    assert not flat.any()
    labels = np.random.default_rng(649).permutation(49)
    permuted, permuted_flat = study.blend_scores(
        left[:, labels], right[:, labels], frequency[:, labels]
    )
    np.testing.assert_array_equal(permuted, score[:, labels])
    np.testing.assert_array_equal(permuted_flat, flat)
    for before, after in zip(original, (left, right, frequency), strict=True):
        np.testing.assert_array_equal(before, after)


@pytest.mark.parametrize("invalid", [np.nan, np.inf, -np.inf])
def test_nonfinite_parent_is_rejected_instead_of_hidden_by_ranking(invalid):
    left = np.arange(49, dtype=float)[None, :]
    right = left.copy()
    right[0, 7] = invalid
    with pytest.raises(ValueError, match="nonfinite"):
        study.blend_scores(left, right, left)


def test_generated_duplicate_is_rejected_before_any_selector(monkeypatch):
    specs = study.candidate_specs()
    score = np.arange(49, dtype=float)[None, :]
    fallback = np.array([False])
    results = {name: (name, score, fallback, 0.0, {}) for name in study.EXPERTS}
    item = (specs[0]["name"], specs[0]["family"], score, fallback)
    monkeypatch.setattr(study, "generate", lambda *_: iter((item, item)))
    with pytest.raises(RuntimeError, match="duplicate"):
        study.all_scores({}, results)


def test_only_consumed_invalid_parents_propagate(monkeypatch):
    score = np.arange(49, dtype=float)[None, :]
    good = np.array([False])
    bad = np.array([True])
    results = {name: (name, score, good, 0.0, {}) for name in study.EXPERTS}
    results["ewma_hl30"] = ("ewma_hl30", score, bad, 0.0, {})
    descriptors = study.candidate_specs()
    monkeypatch.setattr(
        study,
        "generate",
        lambda *_: iter((d["name"], d["family"], score, good) for d in descriptors),
    )
    out = study.all_scores({}, results)
    for descriptor in descriptors:
        expected = "ewma_hl30" in descriptor["parent_names"]
        assert bool(out[descriptor["name"]][1][0]) == expected
        assert bool(out["mix_markov50__" + descriptor["name"]][1][0]) == expected


def test_invalid_frequency_reserve_does_not_contaminate_other_families(monkeypatch):
    score = np.arange(49, dtype=float)[None, :]
    good = np.array([False])
    bad = np.array([True])
    results = {name: (name, score, good, 0.0, {}) for name in study.EXPERTS}
    results["frequency"] = ("frequency", score, bad, 0.0, {})
    descriptors = study.candidate_specs()
    monkeypatch.setattr(
        study,
        "generate",
        lambda *_: iter((d["name"], d["family"], score, good) for d in descriptors),
    )
    out = study.all_scores({}, results)
    for descriptor in descriptors:
        expected = "frequency" in descriptor["parent_names"]
        assert bool(out[descriptor["name"]][1][0]) == expected
        assert bool(out["mix_markov50__" + descriptor["name"]][1][0]) == expected
