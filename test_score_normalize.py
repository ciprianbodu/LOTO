"""Normalizare canonică: finite = bit-identic cu formula veche; NaN nu otrăvește."""
from __future__ import annotations

import math

import numpy as np
import pytest

from loto_enterprise.benchmark.score_normalize import normalize_scores


def _legacy_unsafe(scores: dict[int, float], max_num: int) -> dict[int, float]:
    """Copia veche (pre-sanitize) — doar ca să dovedim identitatea pe finite."""
    if not scores:
        return {n: 0.0 for n in range(1, max_num + 1)}
    vals = np.fromiter(scores.values(), dtype=np.float64)
    vmin, vmax = float(vals.min()), float(vals.max())
    rng = max(vmax - vmin, 1e-12)
    out = {int(k): float((v - vmin) / rng) for k, v in scores.items()}
    for n in range(1, max_num + 1):
        out.setdefault(n, 0.0)
    return out


def test_empty_dict_fills_universe_with_zeros():
    out = normalize_scores({}, 5)
    assert out == {1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0, 5: 0.0}


def test_finite_scores_match_legacy_formula():
    raw = {1: 10.0, 3: 0.0, 5: 5.0}
    got = normalize_scores(raw, 5)
    assert got == _legacy_unsafe(raw, 5)
    assert got[1] == pytest.approx(1.0)
    assert got[3] == pytest.approx(0.0)
    assert got[5] == pytest.approx(0.5)
    assert got[2] == 0.0 and got[4] == 0.0


def test_nan_does_not_poison_other_scores():
    raw = {1: 2.0, 2: 4.0, 3: float("nan")}
    out = normalize_scores(raw, 3)
    assert math.isfinite(out[1]) and math.isfinite(out[2]) and math.isfinite(out[3])
    assert out[2] == pytest.approx(1.0)
    assert out[1] == pytest.approx(0.0)
    assert out[3] == pytest.approx(0.0)


def test_all_nan_returns_zeros():
    out = normalize_scores({1: float("nan"), 2: float("inf")}, 2)
    assert out == {1: 0.0, 2: 0.0}
