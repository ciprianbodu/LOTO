"""Protocol integrity, chronological forecasts and constrained tail search."""
from itertools import combinations
import math

import numpy as np
import pytest

from covering.lr_schemes import lr_schemes
from loto_enterprise.core.ranking import longest_consecutive_run
from scripts.analysis.lr_tail_2026_11_05 import (
    GATE_THRESHOLD, PROTOCOL, PrequentialModels, checked_pool,
    draw_log_likelihood, gate_is_open, optimize_pool, primary_mask, simulate,
)
from scripts.analysis.lr_tail_objective_2026_11_05 import LRTailObjective


def joker_fixture():
    config = PROTOCOL["games"][1]
    scheme = next(s for s in lr_schemes("joker", 15) if s.code == 12)
    rng = np.random.default_rng(20261105)
    rows = np.array([rng.choice(np.arange(1, 46), 5, replace=False) for _ in range(40)])
    dates = np.repeat(np.arange("2020-01-01", "2020-01-21", dtype="datetime64[D]"), 2)
    cutoffs = np.repeat(np.arange(0, 40, 2), 2)
    return config, scheme, rows, dates, cutoffs


def test_fixed_product_likelihood_normalizes_and_same_day_uses_frozen_expert():
    weights = np.array([0.2, 0.7, 1.2, 2.3, 3.0])
    possibilities = list(combinations(range(1, 6), 2))
    probabilities = [math.exp(draw_log_likelihood(np.array([row]), weights))
                     for row in possibilities]
    assert sum(probabilities) == pytest.approx(1.0)
    models = PrequentialModels(5, 2)
    models.complete_day(np.array([[1, 2], [1, 3]]))
    previous = models.snapshot()
    before = models.log_lr.copy()
    day = np.array([[1, 2], [1, 4]])
    expected = np.array([draw_log_likelihood(day, w) for w in previous["weights"]])
    expected += len(day) * math.log(math.comb(5, 2))
    models.complete_day(day)
    assert models.log_lr - before == pytest.approx(expected)
    # Decay is once per observed complete date, not per draw or calendar gap.
    assert models.effective_draws == pytest.approx([4, 2 * models.decay + 2])
    assert models.counts[1] == pytest.approx(
        np.array([2, 1, 1, 0, 0]) * models.decay + np.array([2, 1, 0, 1, 0]))


def test_gate_is_predictive_mixture_bf60_not_posterior_odds_or_max_expert():
    assert GATE_THRESHOLD == 60
    assert not gate_is_open(math.log(60) - 1e-10)
    assert gate_is_open(math.log(60))
    state = PrequentialModels(49, 6)
    state.log_lr = np.log([100.0, 1.0])
    assert state.snapshot()["log_alt_bf"] == pytest.approx(math.log(50.5))
    assert not gate_is_open(state.snapshot()["log_alt_bf"])
    state.log_lr = np.log([120.0, 1.0])
    assert gate_is_open(state.snapshot()["log_alt_bf"])
    with pytest.raises(ValueError, match="Nonfinite"):
        gate_is_open(float("nan"))


def test_uniform_evidence_and_prior_probabilities_at_start():
    state = PrequentialModels(45, 5)
    snapshot = state.snapshot()
    assert snapshot["posterior"] == pytest.approx([.9, .05, .05])
    assert snapshot["log_alt_bf"] == pytest.approx(0)
    state.complete_day(np.array([[1, 2, 3, 4, 5], [41, 42, 43, 44, 45]]))
    assert state.log_lr == pytest.approx([0, 0], abs=1e-12)
    assert state.snapshot()["posterior"] == pytest.approx([.9, .05, .05])


def test_conditional_scaling_preserves_mixture_ranking_and_survives_underflow():
    state = PrequentialModels(45, 5)
    state.complete_day(np.array([[1, 2, 3, 4, 5]]))
    state.log_lr[:] = [-3, -4]
    snap = state.snapshot()
    full = snap["posterior"][0] + snap["posterior"][1:] @ (
        snap["weights"] / snap["weights"].mean(axis=1, keepdims=True))
    scaled = np.array(list(snap["scores"].values()))
    assert full == pytest.approx(snap["posterior"][0] + snap["posterior"][1:].sum() * scaled)
    state.log_lr -= 10000
    tiny = state.snapshot()
    assert tiny["conditional"] == pytest.approx(snap["conditional"])
    assert np.all(np.isfinite(list(tiny["scores"].values())))
    assert not gate_is_open(tiny["log_alt_bf"])


def test_future_and_current_day_changes_cannot_change_same_day_pools():
    config, scheme, matrix, dates, cutoffs = joker_fixture()
    original, _ = simulate(config, matrix, dates, cutoffs, scheme, warmup=8)
    changed = matrix.copy()
    # Alter the first draw of a two-draw day, plus every subsequent draw.
    changed[20:] = 46 - changed[20:]
    mutated, _ = simulate(config, changed, dates, cutoffs, scheme, warmup=8)
    unchanged = original["indices"] < 22
    for name in ("pools", "posterior", "log_alt_bf", "gate_open",
                 "model_tail_start", "model_tail_finish"):
        np.testing.assert_array_equal(original[name][unchanged], mutated[name][unchanged])
    # The update is actually sensitive to outcomes once that day has ended.
    assert not np.array_equal(original["posterior"][~unchanged], mutated["posterior"][~unchanged])
    for i in range(0, len(original["indices"]), 2):
        np.testing.assert_array_equal(original["pools"][i], original["pools"][i + 1])
        np.testing.assert_array_equal(original["posterior"][i], original["posterior"][i + 1])
    closed = ~original["gate_open"]
    np.testing.assert_array_equal(original["pools"][closed, 3], original["pools"][closed, 0])
    assert np.all(original["proposals_evaluated"] <= 48)


@pytest.mark.parametrize("config", PROTOCOL["games"], ids=lambda c: c["key"])
def test_search_preserves_sorted_official_mapping_constraints_and_model_tails(config):
    scheme = next(s for s in lr_schemes(config["label"], config["pool_n"])
                  if s.code == config["code"])
    rng = np.random.default_rng(1961)
    weights = np.exp(rng.normal(0, .4, size=(2, config["universe"])))
    conditional = np.array([.35, .65])
    scores = dict(enumerate(conditional @ (weights / weights.mean(axis=1, keepdims=True)), 1))
    start = checked_pool(scores, config["pool_n"], config["universe"])
    objectives = [LRTailObjective(scheme, dict(enumerate(w, 1))) for w in weights]
    finish, audit = optimize_pool(start, scores, objectives, conditional)
    assert finish == tuple(sorted(finish))
    assert len(set(finish)) == config["pool_n"]
    assert longest_consecutive_run(finish) <= 2
    assert audit["proposals_evaluated"] <= 48
    assert np.all(audit["finish"] >= audit["start"] * (1 - 3e-12))
    actual = sum(p * objective.evaluate_many([finish])[0]
                 for p, objective in zip(conditional, objectives, strict=True))
    assert actual == pytest.approx(audit["finish"])
    if audit["accepted_swaps"]:
        assert np.any(audit["finish"] > audit["start"] * (1 + 1e-12))
    else:
        assert finish == start
    assert optimize_pool(start, scores, objectives, conditional)[0] == finish


def test_uniform_model_has_no_artificial_swap_gain():
    config, scheme, *_ = joker_fixture()
    weights = dict.fromkeys(range(1, 46), 1.0)
    start = checked_pool(weights, config["pool_n"], 45)
    objectives = [LRTailObjective(scheme, weights)] * 2
    finish, audit = optimize_pool(start, weights, objectives, [.5, .5])
    assert finish == start
    assert audit["accepted_swaps"] == 0
    np.testing.assert_array_equal(audit["start"], audit["finish"])


def test_primary_split_keeps_full_dates_and_invalid_cutoffs_fail_closed():
    config, scheme, matrix, dates, cutoffs = joker_fixture()
    mask = primary_mask(dates)
    assert mask.sum() == 12
    assert np.all(mask[::2] == mask[1::2])
    with pytest.raises(ValueError, match="whole-day cutoffs"):
        simulate(config, matrix, dates, np.arange(len(matrix)), scheme, warmup=8)
    with pytest.raises(ValueError, match="Incomplete or nonfinite"):
        checked_pool({1: float("nan")}, 15, 45)
