"""DMD must retain entire singular subspaces at the rank boundary."""

import numpy as np
import pytest

from loto_enterprise.benchmark.methods_common import indicator, vector_to_scores
from loto_enterprise.benchmark.methods_wave2 import (
    _svd_rank_including_ties,
    score_dmd_forecast,
)


@pytest.mark.parametrize(
    "values,requested,expected",
    [
        ([9, 8, 7, 7, 7, 6], 3, 5),
        ([9, 8, 7, 7 - 1e-12, 7 - 2e-12, 6], 3, 5),
        ([9, 8, 7, 7 - 1e-6, 6, 5], 3, 3),
        ([9, 8, 7, 6], 2, 2),
        ([9, 8, 7, 6], 20, 4),
        ([1e-7, 1e-9, 1e-9, 0], 2, 1),
        ([0, 0, 0], 2, 0),
        ([], 3, 0),
        ([9, 8, 7], 0, 0),
    ],
)
def test_boundary_rank_keeps_exact_and_numerical_ties(values, requested, expected):
    assert _svd_rank_including_ties(np.asarray(values, dtype=float), requested) == expected


@pytest.mark.parametrize("relative_noise", [0.0, 5e-12])
def test_single_pick_tied_history_follows_relabeling(monkeypatch, relative_noise):
    # The leading singular subspace of one-hot draws has repeated
    # singular values straddling rank six. The previous arbitrary six-vector
    # truncation changes the forecast when the labels are permuted.
    history = np.random.default_rng(2).integers(1, 21, (200, 1))
    original = history.copy()
    history.flags.writeable = False
    real_svd = np.linalg.svd

    def noisy_svd(*args, **kwargs):
        u, s, vt = real_svd(*args, **kwargs)
        # Model rounding differences between LAPACK implementations without
        # changing the significant singular spectrum.
        s = s * (1.0 + relative_noise * np.linspace(1.0, 0.0, len(s)))
        return u, s, vt

    monkeypatch.setattr(np.linalg, "svd", noisy_svd)
    scores = score_dmd_forecast(history, 20)
    assert max(scores.values()) - min(scores.values()) > 0.5
    rng = np.random.default_rng(20261018)
    for _ in range(12):
        perm = rng.permutation(20) + 1
        moved = score_dmd_forecast(perm[history - 1], 20)
        np.testing.assert_allclose(
            [moved[int(perm[j - 1])] for j in range(1, 21)],
            [scores[j] for j in range(1, 21)],
            rtol=0,
            atol=1e-9,
        )
    np.testing.assert_array_equal(history, original)


@pytest.mark.parametrize("rank", [0, 1, 6, 45])
def test_dmd_unchanged_for_distinct_singular_boundary(rank):
    rng = np.random.default_rng(20261018)
    draws = np.array([rng.choice(np.arange(1, 50), 6, replace=False) for _ in range(240)])
    ind = indicator(draws, 49)
    x = ind[-200:].T
    x = x - x.mean(axis=1, keepdims=True)
    source, target = x[:, :-1], x[:, 1:]
    u, s, vt = np.linalg.svd(source, full_matrices=False)
    retained = min(rank, int((s > 1e-8).sum()))
    if retained == 0:
        expected = vector_to_scores(np.zeros(49), 49)
    else:
        ur, sr, vtr = u[:, :retained], s[:retained], vt[:retained]
        a_tilde = ur.T @ target @ vtr.T / sr
        expected = vector_to_scores((ur @ a_tilde @ ur.T) @ x[:, -1], 49)
    assert score_dmd_forecast(draws, 49, rank=rank) == expected


@pytest.mark.parametrize("length", [0, 1, 19, 20, 200])
def test_dmd_constant_and_short_histories_remain_finite(length):
    history = np.ones((length, 1), dtype=np.int64)
    scores = score_dmd_forecast(history, 20)
    assert len(scores) == 20
    assert all(np.isfinite(v) and 0 <= v <= 1 for v in scores.values())
    if length >= 20:
        assert set(scores.values()) == {0.0}


def test_null_singular_projection_is_flat_instead_of_amplifying_roundoff():
    # The retained leading subspace is orthogonal to the final centered draw.
    # Its mathematical forecast is zero for every ball.
    history = np.tile(np.arange(1, 21), 10).reshape(-1, 1)
    rng = np.random.default_rng(20261018)
    for _ in range(12):
        perm = rng.permutation(20) + 1
        scores = score_dmd_forecast(perm[history - 1], 20)
        assert set(scores.values()) == {0.0}
