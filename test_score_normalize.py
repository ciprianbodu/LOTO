"""Normalizare canonică: finite = bit-identic cu formula veche; NaN nu otrăvește."""
from __future__ import annotations

import math

import numpy as np
import pytest

from loto_enterprise.benchmark.score_normalize import normalize_scores
from loto_enterprise.benchmark.methods import METHODS, call_method


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


def test_blend_skips_nan_component_keeps_finite_ranking():
    """Un component NaN nu mai otrăvește blend-ul: numărul păstrează celelalte semnale."""
    from loto_enterprise.benchmark.methods_search_649 import make_blend_scorer

    def good(_draws, max_num):
        return {n: float(n) for n in range(1, max_num + 1)}

    def bad(_draws, max_num):
        return {n: float("nan") for n in range(1, max_num + 1)}

    out = make_blend_scorer([(0.5, good), (0.5, bad)])(np.zeros((2, 2)), 5)
    assert all(math.isfinite(v) for v in out.values())
    assert out[5] > out[1]


def test_call_method_sanitizes_only_when_nonfinite():
    def _mixed(_draws, _max_num):
        return {1: 2.0, 2: float("nan"), 3: 1.0}

    def _clean(_draws, _max_num):
        return {1: 0.25, 2: 0.75}

    def _empty(_draws, _max_num):
        return {}

    def _none(_draws, _max_num):
        return None

    METHODS["_tmp_mixed"] = (_mixed, "test", False, "tmp")
    METHODS["_tmp_clean"] = (_clean, "test", False, "tmp")
    METHODS["_tmp_empty"] = (_empty, "test", False, "tmp")
    METHODS["_tmp_none"] = (_none, "test", False, "tmp")
    draws = np.array([[1, 2, 3]], dtype=int)
    try:
        mixed, _dt = call_method("_tmp_mixed", draws, 5)
        assert mixed[1] == 1.0
        assert mixed[2] == 0.0
        assert mixed[3] == 0.0

        clean, _dt = call_method("_tmp_clean", draws, 5)
        assert clean == {1: 0.25, 2: 0.75}

        empty, _dt = call_method("_tmp_empty", draws, 5)
        assert empty == {}

        none, _dt = call_method("_tmp_none", draws, 5)
        assert none == {}
    finally:
        del METHODS["_tmp_mixed"]
        del METHODS["_tmp_clean"]
        del METHODS["_tmp_empty"]
        del METHODS["_tmp_none"]
