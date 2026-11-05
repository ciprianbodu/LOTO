"""Synthetic-only causal/formula checks for the new adaptive research bank."""

import json
import math
from itertools import combinations, pairwise, product

import numpy as np
import pytest
from scipy.special import betaln, logsumexp
from scipy.stats import betabinom, rankdata

from scripts.analysis import pool_adaptive_candidates_2026_10_19 as bank


def _fixture(n=40, *, same_day=2, start=12, seed=938):
    rng = np.random.default_rng(seed)
    draws = np.asarray([rng.choice(49, 6, replace=False) + 1 for _ in range(n)])
    dates = np.datetime64("2000-01-01") + np.arange(n) // same_day
    targets = np.arange(start, n, dtype=np.int64)
    # Discrete scores exercise partial mean-rank ties, without outcome access.
    experts = {
        name: rng.integers(0, 20, size=(len(targets), 49)).astype(float)
        for name in bank.EXPERT_NAMES
    }
    data = {
        "draws": draws,
        "dates": dates,
        "targets": targets,
        "frequency_past": experts["frequency"].copy(),
    }
    return data, experts


def _results(data, experts):
    return {
        name: (family, values, fallback)
        for name, family, values, fallback in bank.generate(data, experts)
    }


def _prefix(data, experts, end):
    selected = data["targets"] < end
    chopped = {
        "draws": data["draws"][:end],
        "dates": data["dates"][:end],
        "targets": data["targets"][selected],
        "frequency_past": data["frequency_past"][selected],
    }
    scores = {name: values[selected] for name, values in experts.items()}
    return chopped, scores


def test_registry_and_contract_are_fixed_finite_and_do_not_mutate_inputs():
    data, experts = _fixture()
    original = {key: values.copy() for key, values in data.items()}
    original_experts = {key: values.copy() for key, values in experts.items()}
    specs = bank.candidate_specs()
    json.dumps(specs, allow_nan=False)
    assert len(specs) == len({spec["name"] for spec in specs}) == 16
    assert [spec["complexity"] for spec in specs] == [1] * 4 + [2] * 12
    assert [spec["family"] for spec in specs].count("bocpd_beta_binomial_daily") == 4
    assert all(set(spec["expert_names"]) <= set(bank.EXPERT_NAMES) for spec in specs)
    results = _results(data, experts)
    assert list(results) == [spec["name"] for spec in specs]
    for family, scores, fallback in results.values():
        assert scores.shape == (len(data["targets"]), 49)
        assert fallback.dtype == np.bool_
        assert fallback.shape == (len(data["targets"]),)
        assert np.isfinite(scores).all()
        assert np.all(np.ptp(scores, axis=1) > 1e-12)
        np.testing.assert_array_equal(
            scores[fallback], data["frequency_past"][fallback]
        )
    for key, value in original.items():
        np.testing.assert_array_equal(value, data[key])
    for key, value in original_experts.items():
        np.testing.assert_array_equal(value, experts[key])


def _bocpd_partition_oracle(actual, starts, ends, hazard, strength):
    """Independent enumeration of EVERY partition, not run-length recursion."""
    predictions = np.empty((len(starts), 49))
    predictions[0] = bank.P0
    a0, b0 = strength * bank.P0, strength * (1.0 - bank.P0)
    y_day = np.asarray([actual[s:e].sum(axis=0) for s, e in zip(starts, ends)])
    b_day = ends - starts
    for day in range(1, len(starts)):
        log_weights, last_means = [], []
        for cuts in product((0, 1), repeat=day - 1):
            changes = sum(cuts)
            log_weight = np.full(
                49,
                changes * math.log(hazard) + (day - 1 - changes) * math.log1p(-hazard),
            )
            boundaries = [0] + [i + 1 for i, cut in enumerate(cuts) if cut] + [day]
            for start, end in pairwise(boundaries):
                successes = y_day[start:end].sum(axis=0)
                total = b_day[start:end].sum()
                log_weight += betaln(a0 + successes, b0 + total - successes) - betaln(
                    a0, b0
                )
                if end == day:
                    last_mean = (a0 + successes) / (a0 + b0 + total)
            # The per-day combinatorial factors are common to every partition.
            log_weights.append(log_weight)
            last_means.append(last_mean)
        log_weights = np.asarray(log_weights)
        posterior = np.exp(log_weights - logsumexp(log_weights, axis=0))
        predictions[day] = hazard * bank.P0 + (1.0 - hazard) * (
            posterior * np.asarray(last_means)
        ).sum(axis=0)
    return predictions


def test_exact_bocpd_matches_partition_enumeration_with_two_draws_per_day():
    data, experts = _fixture(n=10, same_day=2, start=0)
    _, actual, starts, ends, targets, positions, _ = bank._validate(data, experts)
    scores = bank._bocpd(actual, starts, ends, targets, positions)
    for hi, hazard_days in enumerate(bank.BOCPD_HAZARD_DAYS):
        for ki, strength in enumerate(bank.PRIOR_STRENGTHS):
            expected = _bocpd_partition_oracle(
                actual, starts, ends, 1 / hazard_days, strength
            )
            np.testing.assert_allclose(
                scores[2 * hi + ki], np.repeat(expected, 2, axis=0), rtol=0, atol=2e-14
            )


@pytest.mark.parametrize("trials", [1, 2, 3, 7])
def test_beta_binomial_closed_form_matches_scipy(trials):
    rng = np.random.default_rng(134)
    a = rng.uniform(0.1, 200, (6, 49))
    beta = rng.uniform(0.1, 200, (6, 49))
    y = rng.integers(0, trials + 1, 49)
    expected = betabinom.pmf(y, trials, a, beta)
    np.testing.assert_allclose(
        bank._beta_binomial_likelihood(a, beta, y, trials),
        expected,
        rtol=2e-12,
        atol=1e-14,
    )


def _naive_contrast(data, experts, target_position, strength, amplitude):
    target = data["targets"][target_position]
    dates = data["dates"]
    cutoff = int(np.flatnonzero(dates == dates[target])[0])
    actual = np.zeros((len(dates), 49), dtype=np.int64)
    actual[np.arange(len(dates))[:, None], data["draws"] - 1] = 1
    base = (rankdata(experts["markov_lag2"][target_position]) - 1) / 48
    if cutoff <= 2:
        return base
    source, response = actual[: cutoff - 2], actual[2:cutoff]
    marginal = response.mean(axis=0)
    terms = []
    for a, b in combinations(data["draws"][cutoff - 2] - 1, 2):
        q = {}
        for x, y in product((0, 1), repeat=2):
            mask = (source[:, a] == x) & (source[:, b] == y)
            q[x, y] = (response[mask].sum(axis=0) + strength * marginal) / (
                int(mask.sum()) + strength
            )
        terms.append((q[1, 1] + q[0, 0]) - (q[1, 0] + q[0, 1]))
    contrast = np.asarray([math.fsum(column) / 15 for column in np.asarray(terms).T])
    if np.ptp(contrast) <= 1e-12:
        return base
    return base + amplitude * ((rankdata(contrast) - 1) / 48 - 0.5)


def test_incremental_pair_contrast_matches_prefix_rescan_oracle():
    data, experts = _fixture(n=50, same_day=2, start=2)
    results = _results(data, experts)
    for strength in bank.PRIOR_STRENGTHS:
        for amp in bank.CONTRAST_AMPLITUDES:
            name = f"lag2_pair_contrast_k{strength}_a{int(amp * 100)}"
            for target_position in (0, 1, 2, 3, 8, 17, 28, 47):
                expected = _naive_contrast(
                    data, experts, target_position, strength, amp
                )
                np.testing.assert_array_equal(
                    results[name][1][target_position], expected
                )


def _naive_calibration(data, experts, partner, nodes, half_life):
    t = len(data["targets"])
    output = np.empty((t, 49))
    state_n, state_h = np.zeros(nodes * nodes), np.zeros(nodes * nodes)
    dates = data["dates"][data["targets"]]
    for day in np.unique(dates):
        positions = np.flatnonzero(dates == day)
        increment_n, increment_h = np.zeros(nodes * nodes), np.zeros(nodes * nodes)
        probabilities = (state_h + 100 * bank.P0) / (state_n + 100)
        for pos in positions:
            axes = [
                (rankdata(experts[name][pos]) - 1) / 48
                for name in ("markov_lag2", partner)
            ]
            targets = set(data["draws"][data["targets"][pos]] - 1)
            for ball in range(49):
                x, y = (axis[ball] * (nodes - 1) for axis in axes)
                ix, iy = min(int(x), nodes - 2), min(int(y), nodes - 2)
                fx, fy = x - ix, y - iy
                prediction = 0.0
                for dx, dy in product((0, 1), repeat=2):
                    weight = (fx if dx else 1 - fx) * (fy if dy else 1 - fy)
                    node = (ix + dx) * nodes + iy + dy
                    prediction += weight * probabilities[node]
                    increment_n[node] += weight
                    increment_h[node] += weight * (ball in targets)
                output[pos, ball] = prediction
        if half_life is not None:
            decay = 2 ** (-len(positions) / half_life)
            state_n *= decay
            state_h *= decay
        state_n += increment_n
        state_h += increment_h
    return output


@pytest.mark.parametrize("partner", bank.CALIBRATION_PARTNERS)
@pytest.mark.parametrize("nodes", bank.CALIBRATION_NODES)
@pytest.mark.parametrize("half_life", bank.CALIBRATION_MEMORIES)
def test_bilinear_calibration_matches_naive_soft_cell_oracle(partner, nodes, half_life):
    data, experts = _fixture(n=28, start=12)
    validated = bank._validate(data, experts)
    scores = bank._calibration_predictions(
        validated[1],
        validated[2],
        validated[3],
        validated[4],
        validated[5],
        validated[6],
    )
    pi = bank.CALIBRATION_PARTNERS.index(partner)
    ni = bank.CALIBRATION_NODES.index(nodes)
    mi = bank.CALIBRATION_MEMORIES.index(half_life)
    expected = _naive_calibration(data, experts, partner, nodes, half_life)
    np.testing.assert_allclose(
        scores[pi * 4 + ni * 2 + mi], expected, rtol=0, atol=1e-16
    )


@pytest.mark.parametrize("end", [13, 14, 25, 32])
def test_truncated_prefixes_reproduce_all_predictions_including_partial_day(end):
    data, experts = _fixture()
    original = _results(data, experts)
    prefix_data, prefix_experts = _prefix(data, experts, end)
    chopped = _results(prefix_data, prefix_experts)
    count = len(prefix_data["targets"])
    for name, (_, scores, mask) in chopped.items():
        np.testing.assert_array_equal(scores, original[name][1][:count])
        np.testing.assert_array_equal(mask, original[name][2][:count])


def test_current_date_and_future_outcomes_cannot_change_any_current_date_prediction():
    data, experts = _fixture()
    original = _results(data, experts)
    changed = dict(data)
    changed["draws"] = data["draws"].copy()
    # Prefix through day 11 (raw rows 22/23) is held fixed; both rows change.
    changed["draws"][22:] = ((changed["draws"][22:] + 12) % 49) + 1
    altered = _results(changed, experts)
    keep = data["targets"] < 24
    assert any(
        not np.array_equal(original[name][1][~keep], altered[name][1][~keep])
        for name in original
    )
    for name in original:
        np.testing.assert_array_equal(original[name][1][keep], altered[name][1][keep])
        np.testing.assert_array_equal(original[name][2][keep], altered[name][2][keep])


def test_calibrators_learn_only_target_stream_not_raw_warmup():
    data, experts = _fixture()
    original = _results(data, experts)
    changed = dict(data)
    changed["draws"] = data["draws"].copy()
    changed["draws"][:12] = ((changed["draws"][:12] + 7) % 49) + 1
    altered = _results(changed, experts)
    for name in original:
        if name.startswith("rank_bilinear"):
            np.testing.assert_array_equal(original[name][1], altered[name][1])


def test_flat_rows_fall_back_exactly_with_audit_and_canonical_topk():
    from loto_enterprise.core.ranking import rank_by_score

    data, experts = _fixture(n=16, start=0)
    results = _results(data, experts)
    for name, (_, scores, mask) in results.items():
        if name.startswith(("bocpd", "rank_bilinear")):
            assert mask[:2].all()
            np.testing.assert_array_equal(scores[:2], experts["frequency"][:2])
            for row in (0, 1):
                score_dict = dict(enumerate(scores[row], 1))
                freq_dict = dict(enumerate(experts["frequency"][row], 1))
                assert rank_by_score(score_dict, 16) == rank_by_score(freq_dict, 16)
    bad = np.full((3, 49), 1.0)
    bad[1, 0] = np.nan
    bad[2] = np.arange(49)
    frequency = np.tile(np.arange(49), (3, 1)).astype(float)
    scores, mask = bank._finished(bad, frequency)
    np.testing.assert_array_equal(mask, [True, True, False])
    np.testing.assert_array_equal(scores[:2], frequency[:2])


@pytest.mark.parametrize("seed,symmetric", [(31, False), (77, False), (31, True)])
def test_ball_relabelling_preserves_scores_even_with_rare_symmetric_states(
    seed, symmetric
):
    data, experts = _fixture(n=48, start=8, seed=seed)
    if symmetric:
        # Many identical response histories, zero state cells and partial ties.
        data["draws"][:] = np.array([1, 2, 3, 4, 5, 6])
        data["draws"][::3] = np.array([7, 8, 9, 10, 11, 12])
    original = _results(data, experts)
    permutation = np.random.default_rng(seed + 100).permutation(49)
    transformed = dict(data)
    transformed["draws"] = permutation[data["draws"] - 1] + 1
    relabelled = {name: np.empty_like(scores) for name, scores in experts.items()}
    for name, scores in experts.items():
        relabelled[name][:, permutation] = scores
    transformed["frequency_past"] = relabelled["frequency"]
    altered = _results(transformed, relabelled)
    for name in original:
        np.testing.assert_array_equal(
            altered[name][1][:, permutation], original[name][1]
        )
        np.testing.assert_array_equal(altered[name][2], original[name][2])


def test_symmetric_pair_contrasts_keep_partial_ties_and_do_not_amplify_zero():
    data, experts = _fixture(n=20, start=4)
    data["draws"][:] = np.array([1, 2, 3, 4, 5, 6])
    for strength in bank.PRIOR_STRENGTHS:
        for position in range(len(data["targets"])):
            base = (rankdata(experts["markov_lag2"][position]) - 1) / 48
            actual = _naive_contrast(data, experts, position, strength, 0.5)
            np.testing.assert_array_equal(actual, base)


@pytest.mark.parametrize(
    "kind",
    ["dates", "groups", "prior", "targets", "duplicate", "frequency", "nonfinite"],
)
def test_malformed_inputs_fail_closed(kind):
    data, experts = _fixture()
    if kind == "dates":
        data["dates"][3] = np.datetime64("1990-01-01")
    elif kind == "groups":
        data["groups"] = np.arange(len(data["draws"]))
    elif kind == "prior":
        data["prior"] = np.arange(len(data["draws"]))
    elif kind == "targets":
        data["targets"] = data["targets"][::-1]
    elif kind == "duplicate":
        data["draws"][3, 1] = data["draws"][3, 0]
    elif kind == "frequency":
        data["frequency_past"][:] = 0
    else:
        experts["ewma_hl30"][0, 0] = np.nan
    with pytest.raises(ValueError):
        list(bank.generate(data, experts))


def test_empty_target_stream_and_canonical_day_metadata():
    data, experts = _fixture(n=8, start=8)
    data["groups"] = np.arange(8) // 2
    data["prior"] = 2 * (np.arange(8) // 2)
    results = _results(data, experts)
    assert len(results) == 16
    assert all(
        scores.shape == (0, 49) and mask.shape == (0,)
        for _, scores, mask in results.values()
    )
