"""Exact-equivalence checks for the scoring primitives optimized on CPython 3.14."""

from __future__ import annotations

import numpy as np
import pytest

from loto_enterprise.benchmark import methods_common as common
from loto_enterprise.benchmark import methods_recency as recency


def reference_last_seen(ind: np.ndarray) -> np.ndarray:
    n = ind.shape[0]
    if n == 0:
        return np.full(ind.shape[1], -1, dtype=np.int64)
    indices = np.where(ind > 0, np.arange(n, dtype=np.int64)[:, None], -1)
    return indices.max(axis=0)


def reference_vector_to_scores(vec, max_num):
    v = np.asarray(vec, dtype=np.float64).reshape(-1)
    if v.shape[0] != int(max_num):
        full = np.zeros(int(max_num), dtype=np.float64)
        full[: min(v.shape[0], int(max_num))] = v[: int(max_num)]
        v = full
    finite = np.isfinite(v)
    if not finite.any():
        return {i + 1: 0.0 for i in range(int(max_num))}
    vmin = float(v[finite].min())
    vmax = float(v[finite].max())
    value_range = max(vmax - vmin, 1e-12)
    out = np.where(finite, (v - vmin) / value_range, 0.0)
    return {i + 1: float(out[i]) for i in range(int(max_num))}


@pytest.mark.parametrize("n", [0, 1, 12, 64, 400, 3000])
@pytest.mark.parametrize("m", [0, 20, 40, 45, 49])
@pytest.mark.parametrize("layout", ["c", "fortran", "strided", "reversed"])
def test_last_seen_matches_reference_for_all_layouts(n, m, layout):
    rng = np.random.default_rng(n + m)
    ind = (rng.random((n, m)) < 0.12).astype(np.float64)
    if m:
        ind[:, 0] = 0  # A number which has never appeared.
    if layout == "fortran":
        ind = np.asfortranarray(ind)
    elif layout == "strided":
        ind = ind[::2, ::2]
    elif layout == "reversed":
        ind = ind[::-1, ::-1]
    before = ind.copy()
    actual = common.last_seen_index(ind)
    np.testing.assert_array_equal(actual, reference_last_seen(ind))
    np.testing.assert_array_equal(ind, before)
    assert actual.dtype == np.int64


def test_last_seen_positive_semantics_and_nonfinite_values():
    ind = np.array(
        [[1, np.inf, -np.inf, np.nan, -1], [0, np.nan, 0, 0.25, -2]],
        dtype=np.float64,
    )
    np.testing.assert_array_equal(common.last_seen_index(ind), [0, 0, -1, 1, -1])


@pytest.mark.parametrize("m", [0, 20, 40, 45, 49])
@pytest.mark.parametrize(
    "values",
    [
        [],
        [np.nan, np.inf, -np.inf],
        [0.0] * 55,
        [0.0, -0.0, 1e-13, -1e-13],
        [-3.1, 2.2, np.nan, 2.2, np.inf, -np.inf],
        np.random.default_rng(314).normal(size=65),
    ],
)
def test_vector_to_scores_preserves_values_order_and_python_types(m, values):
    with np.errstate(invalid="ignore"):
        actual = common.vector_to_scores(values, m)
        expected = reference_vector_to_scores(values, m)
    assert list(actual.items()) == list(expected.items())
    assert all(type(k) is int and type(v) is float for k, v in actual.items())


@pytest.mark.parametrize("geometry", [(49, 6), (40, 6), (45, 5), (20, 1)])
def test_gap_scorers_remain_bit_identical(geometry, monkeypatch):
    max_num, draw_n = geometry
    rng = np.random.default_rng(314)
    draws = np.array(
        [rng.choice(max_num, draw_n, replace=False) + 1 for _ in range(750)]
    )
    functions = [
        recency.score_gap_current,
        recency.score_gap_ratio,
        recency.score_gap_hazard,
        recency.score_rhythm_phase,
    ]
    actual = [fn(draws, max_num) for fn in functions]
    monkeypatch.setattr(recency, "last_seen_index", reference_last_seen)
    monkeypatch.setattr(recency, "vector_to_scores", reference_vector_to_scores)
    expected = [fn(draws, max_num) for fn in functions]
    assert actual == expected
