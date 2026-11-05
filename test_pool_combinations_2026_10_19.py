"""Outcome-free contracts and invariants of the second pool-combination bank."""

from __future__ import annotations

from collections import Counter
import json

import numpy as np
import pytest
from scipy.stats import rankdata

from scripts.analysis import pool_combinations_2026_10_19 as combinations


def _fixture(rows=4, methods=10):
    names = list(combinations.SEMANTIC_EXPERTS)
    names.extend(f"synthetic_expert_{j:02d}" for j in range(methods - len(names)))
    rng = np.random.default_rng(20820261019)
    values = rng.normal(size=(rows, len(names), 49))
    values[:, names.index("frequency"), :] = rng.uniform(size=(rows, 49))
    return values, names


def _bank(values, names):
    return {
        name: scores
        for name, _family, scores in combinations.iter_candidates(values, names)
    }


def test_47_experts_define_208_explicit_specs_without_outcomes():
    _scores, names = _fixture(methods=47)
    specs = combinations.candidate_specs(names)
    assert len(specs) == len({spec["name"] for spec in specs}) == 208
    assert Counter(spec["family"] for spec in specs) == {
        "pair_mean_rank": 138,
        "robust_rank": 8,
        "rrf_rank": 12,
        "geometric_rank": 4,
        "residual_rank": 46,
    }
    for spec in specs:
        assert spec["complexity"] == len(set(spec["parent_names"]))
        assert set(spec["parent_names"]) <= set(names)
        assert spec["flat_fallback"] == "frequency"
    json.dumps(specs)


def test_every_spec_yields_one_finite_matrix_and_inputs_are_unchanged():
    values, names = _fixture(methods=47)
    before = values.copy()
    audit = {}
    generated = list(combinations.iter_candidates(values, names, audit=audit))
    specs = combinations.candidate_specs(names)
    assert [name for name, _family, _scores in generated] == [
        spec["name"] for spec in specs
    ]
    assert len(audit) == len(generated) == 208
    for name, _family, scores in generated:
        assert scores.shape == (len(values), 49) and np.isfinite(scores).all()
        assert audit[name].shape == (len(values),) and audit[name].dtype == np.bool_
    np.testing.assert_array_equal(values, before)


def test_input_method_order_does_not_change_spec_or_generated_score():
    values, names = _fixture()
    order = np.random.default_rng(7).permutation(len(names))
    names_permuted = [names[i] for i in order]
    assert combinations.candidate_specs(names) == combinations.candidate_specs(
        names_permuted
    )
    first = _bank(values, names)
    permuted = _bank(values[:, order], names_permuted)
    for name in first:
        np.testing.assert_array_equal(first[name], permuted[name])


def test_relabelling_balls_relabels_every_score_without_jitter():
    values, names = _fixture()
    # Include exact ties to check average-rank semantics as well as unique values.
    values[:, names.index("markov_pairs")] = np.round(
        values[:, names.index("markov_pairs")]
    )
    order = np.random.default_rng(77).permutation(49)
    first = _bank(values, names)
    permuted = _bank(values[:, :, order], names)
    for name in first:
        np.testing.assert_array_equal(first[name][:, order], permuted[name])


def test_prefix_and_future_changes_do_not_change_any_past_score():
    values, names = _fixture(rows=7)
    full = _bank(values, names)
    prefix = _bank(values[:3], names)
    changed = values.copy()
    changed[3:] = -changed[3:]
    future = _bank(changed, names)
    for name in full:
        np.testing.assert_array_equal(full[name][:3], prefix[name])
        np.testing.assert_array_equal(full[name][:3], future[name][:3])


def test_weighted_pair_uses_average_ranks_exactly():
    values, names = _fixture(rows=1)
    values[0, names.index("markov_pairs")] = np.repeat(
        [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0], 7
    )
    ranked = (rankdata(values, axis=-1, method="average") - 1) / 48
    actual = _bank(values, names)["combo_rank_m025__markov_pairs"]
    expected = (
        0.25 * ranked[:, names.index("markov_lag2")]
        + 0.75 * ranked[:, names.index("markov_pairs")]
    )
    np.testing.assert_array_equal(actual, expected)


def test_opposite_equal_weight_rankings_fall_back_to_exact_frequency_and_audit():
    values, names = _fixture(rows=2)
    markov = names.index("markov_lag2")
    pair = names.index("markov_pairs")
    values[0, markov] = np.arange(49)
    values[0, pair] = -np.arange(49)
    audit = {}
    generated = {
        name: score
        for name, _family, score in combinations.iter_candidates(
            values, names, audit=audit
        )
    }
    name = "combo_rank_m050__markov_pairs"
    np.testing.assert_array_equal(audit[name], [True, False])
    np.testing.assert_array_equal(
        generated[name][0], values[0, names.index("frequency")]
    )
    assert not np.array_equal(generated[name][1], values[1, names.index("frequency")])


def test_robust_group_formulas_and_rrf_geometric_definitions():
    values, names = _fixture()
    ranked = rankdata(values, axis=-1, method="average")
    quality = (ranked - 1) / 48
    positions = 50 - ranked
    generated = _bank(values, names)
    for group, members in combinations.GROUPS.items():
        idx = [names.index(name) for name in members]
        group_quality = quality[:, idx]
        np.testing.assert_array_equal(
            generated[f"combo_median__{group}"], np.median(group_quality, axis=1)
        )
        trim = int(len(members) * 0.2)
        expected = np.sort(group_quality, axis=1)[:, trim : len(members) - trim].mean(
            axis=1
        )
        np.testing.assert_array_equal(generated[f"combo_trim20__{group}"], expected)
        for offset in (0, 10, 60):
            np.testing.assert_array_equal(
                generated[f"combo_rrf_c{offset:02d}__{group}"],
                (1 / (offset + positions[:, idx])).sum(axis=1),
            )
        np.testing.assert_array_equal(
            generated[f"combo_geomean__{group}"],
            -np.exp(np.log(positions[:, idx]).mean(axis=1)),
        )


def test_residual_is_orthogonal_to_centered_anchor_and_unchanged_for_identical_expert():
    values, names = _fixture()
    base = (rankdata(values[:, names.index("markov_lag2")], axis=-1) - 1) / 48
    generated = _bank(values, names)
    correction = (generated["combo_residual_a050__markov_pairs"] - base) / 0.5
    np.testing.assert_allclose(
        np.einsum("tn,tn->t", correction, base - 0.5), 0, rtol=0, atol=2e-15
    )
    identical = values.copy()
    identical[:, names.index("markov_pairs")] = identical[:, names.index("markov_lag2")]
    result = _bank(identical, names)["combo_residual_a050__markov_pairs"]
    np.testing.assert_array_equal(result, base)


def test_fixed_semantic_groups_do_not_expand_with_other_experts():
    values, names = _fixture(methods=47)
    subset = _bank(values[:, :10], names[:10])
    complete = _bank(values, names)
    for spec in combinations.candidate_specs(names[:10]):
        if spec["complexity"] > 2:
            np.testing.assert_array_equal(subset[spec["name"]], complete[spec["name"]])


def test_empty_time_axis_has_valid_empty_outputs():
    values, names = _fixture(rows=0)
    generated = list(combinations.iter_candidates(values, names))
    assert len(generated) == 60
    assert all(scores.shape == (0, 49) for _name, _family, scores in generated)


@pytest.mark.parametrize(
    "kind", ["shape", "nonfinite", "flat_frequency", "names_duplicate", "names_missing"]
)
def test_invalid_input_contract_is_rejected(kind):
    values, names = _fixture()
    if kind == "shape":
        values = values[:, :, :48]
    elif kind == "nonfinite":
        values[0, 0, 0] = np.nan
    elif kind == "flat_frequency":
        values[:, names.index("frequency")] = 1
    elif kind == "names_duplicate":
        names[-1] = names[0]
    elif kind == "names_missing":
        names[-1] = "not_consistency"
    with pytest.raises(ValueError):
        list(combinations.iter_candidates(values, names))
