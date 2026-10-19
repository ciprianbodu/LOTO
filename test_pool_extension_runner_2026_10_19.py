"""Generic mean ranks and consumed-parent audit for both requested geometries."""

import json

import numpy as np
import pytest
from scipy.stats import rankdata

from scripts.analysis import pool_extension_2026_10_19 as study


@pytest.mark.parametrize("game", ["loto_5_40", "joker_urna1"])
def test_blend_retains_partial_ties_label_equivariance_and_exact_fallback(game):
    n = study.GAMES[game]["max_num"]
    left = np.floor(np.arange(n, dtype=float) / 3)[None, :]
    right = np.roll(left, 3, axis=1)
    frequency = np.arange(n, dtype=float)[None, :] ** 2
    score, flat = study.blend_scores(left, right, frequency)
    expected = (
        rankdata(left, axis=1, method="average")
        + rankdata(right, axis=1, method="average")
        - 2
    ) / (2 * (n - 1))
    np.testing.assert_array_equal(score, expected)
    assert not flat.any()
    labels = np.random.default_rng(540).permutation(n)
    changed, changed_flat = study.blend_scores(
        left[:, labels], right[:, labels], frequency[:, labels]
    )
    np.testing.assert_array_equal(changed, score[:, labels])
    np.testing.assert_array_equal(changed_flat, flat)
    fallback, flat = study.blend_scores(left, -left, frequency)
    np.testing.assert_array_equal(fallback, frequency)
    assert flat.all()


@pytest.mark.parametrize("bad", [np.nan, np.inf, -np.inf])
def test_invalid_blend_parent_fails_before_ranking(bad):
    left = np.arange(40, dtype=float)[None, :]
    right = left.copy()
    right[0, 7] = bad
    with pytest.raises(ValueError, match="nonfinite"):
        study.blend_scores(left, right, left)


@pytest.mark.parametrize("game", ["loto_5_40", "joker_urna1"])
@pytest.mark.parametrize("bad_parent", ["frequency", "ewma_hl30", "markov_lag2"])
def test_only_consumed_parents_propagate_invalidity(monkeypatch, game, bad_parent):
    meta = study.GAMES[game]
    n = meta["max_num"]
    score = np.arange(n, dtype=float)[None, :]
    good = np.array([False])
    bad = np.array([True])
    results = {
        name: (name, score, bad if name == bad_parent else good, 0.0, {})
        for name in study.EXPERTS
    }
    descriptions = study.candidate_specs(n, meta["draw_n"])
    monkeypatch.setattr(
        study,
        "generate",
        lambda *_a, **_kw: iter(
            (d["name"], d["family"], score, good) for d in descriptions
        ),
    )
    out = study.all_scores({}, results, meta)
    for descriptor in descriptions:
        expected = bad_parent in descriptor["parent_names"]
        assert bool(out[descriptor["name"]][1][0]) == expected
        assert bool(out["mix_markov50__" + descriptor["name"]][1][0]) == (
            expected or bad_parent == "markov_lag2"
        )


def test_duplicate_generator_id_is_rejected(monkeypatch):
    meta = study.GAMES["joker_urna1"]
    score = np.arange(45, dtype=float)[None, :]
    valid = np.array([False])
    d = study.candidate_specs(45, 5)[0]
    item = (d["name"], d["family"], score, valid)
    results = {name: (name, score, valid, 0.0, {}) for name in study.EXPERTS}
    monkeypatch.setattr(study, "generate", lambda *_a, **_kw: iter((item, item)))
    with pytest.raises(RuntimeError, match="duplicate"):
        study.all_scores({}, results, meta)


def test_current_diagnostic_ids_are_frozen_without_silently_accepting_unknowns(
    tmp_path, monkeypatch
):
    saved = {
        "games": {
            g: {
                "auto_pilot_per_pool": {
                    f"k{k}": {"scorer": "frequency"} for k in m["ks"]
                }
            }
            for g, m in study.GAMES.items()
        }
    }
    path = tmp_path / "best_methods.json"
    path.write_text(json.dumps(saved), encoding="utf-8")
    monkeypatch.setattr(study, "ROOT", tmp_path)
    expected = {
        g: {f"k{k}": "frequency" for k in m["ks"]} for g, m in study.GAMES.items()
    }
    assert study.current_methods() == expected
    saved["games"]["joker_urna1"]["auto_pilot_per_pool"]["k19"]["scorer"] = (
        "unknown_method"
    )
    path.write_text(json.dumps(saved), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid current"):
        study.current_methods()
