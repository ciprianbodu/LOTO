"""Combination research may reuse scores only under exact causal identities."""

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts.analysis import pool_combination_cache_2026_10_19 as reuse
from scripts.analysis import pool_score_cache_2026_10_19 as base


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    rng = np.random.default_rng(20261019)
    draws = np.array([rng.choice(49, 6, replace=False) + 1 for _ in range(300)])
    frame = pd.DataFrame(draws, columns=[f"n{i}" for i in range(1, 7)])
    dates = pd.date_range("2020-01-01", periods=300).strftime("%d-%m-%Y").tolist()
    dates[261] = dates[260]
    frame.insert(0, "date", dates)
    path = tmp_path / "synthetic.csv"
    frame.to_csv(path, index=False)
    old_root, new_root = tmp_path / "old", tmp_path / "new"
    monkeypatch.setattr(reuse, "LEGACY_CACHE_ROOT", old_root)
    monkeypatch.setattr(reuse, "COMBINATION_CACHE_ROOT", new_root)
    result = base.evaluate_method(
        {
            "path": path,
            "name": "markov_lag2",
            "cache_dir": old_root / "country",
            "identity": "old_protocol",
            "start": 256,
        }
    )
    old_path = next(old_root.rglob("*.npz"))
    with np.load(old_path, allow_pickle=False) as archive:
        metadata = json.loads(str(archive["metadata"].item()))
    return path, old_root, new_root, old_path, metadata, result


def test_reuses_different_protocol_identity_with_exact_target_slice(
    prepared, monkeypatch
):
    path, old_root, new_root, old_path, metadata, old = prepared
    before = old_path.read_bytes()
    old_data = base.load_data(path)

    def forbidden(*args, **kwargs):
        raise AssertionError("compatible raw scores must not be recomputed")

    monkeypatch.setattr(base, "evaluate_method", forbidden)
    data, results = reuse.load_scores(
        path, ["markov_lag2"], new_root, "new_protocol", workers=1, start=280
    )
    positions = np.searchsorted(old_data["targets"], data["targets"])
    assert data["input_sha"] == old_data["input_sha"]
    np.testing.assert_array_equal(old_data["targets"][positions], data["targets"])
    np.testing.assert_array_equal(results["markov_lag2"][1], old[1][positions])
    assert results["markov_lag2"][3] == old[3]
    assert old_path.read_bytes() == before
    assert len(list(old_root.rglob("*.npz"))) == 1
    report = data["score_cache_provenance"]["markov_lag2"]
    assert report["source_cache_sha256"] == hashlib.sha256(before).hexdigest()
    assert report["source_metadata"]["identity"] == "old_protocol"
    assert report["target_metadata"]["identity"] == "new_protocol"
    assert report["slice_positions"] == positions.tolist()
    sidecar = next(new_root.rglob("*.provenance.json"))
    assert (
        json.loads(sidecar.read_text())["new_target_indices"]
        == data["targets"].tolist()
    )
    again, cached = reuse.load_scores(
        path, ["markov_lag2"], new_root, "new_protocol", workers=1, start=280
    )
    np.testing.assert_array_equal(cached["markov_lag2"][1], results["markov_lag2"][1])


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", 999),
        ("source_sha", "changed"),
        ("input_sha", "changed"),
        ("code_source_sha", "changed"),
        ("python", [3, 14, 99]),
        ("numpy", "different"),
        ("method", "frequency"),
        ("start", 281),
    ],
)
def test_incompatible_cache_recomputes_current_method(
    prepared, monkeypatch, field, value
):
    path, old_root, new_root, old_path, metadata, old = prepared
    altered = {**metadata, field: value}
    base._write_cache(old_path, altered, *old[1:])
    original = base.evaluate_method
    calls = []

    def observed(args):
        calls.append(args["name"])
        return original(args)

    monkeypatch.setattr(base, "evaluate_method", observed)
    data, results = reuse.load_scores(
        path, ["markov_lag2"], new_root, "new_protocol", workers=1, start=280
    )
    assert calls == ["markov_lag2"]
    assert not results["markov_lag2"][2].any()
    assert (
        data["score_cache_provenance"]["markov_lag2"]["mode"] == "computed_current_code"
    )


def test_single_fallback_reason_is_resliced_exactly(prepared, monkeypatch):
    path, _, new_root, old_path, metadata, old = prepared
    scores, invalid = old[1].copy(), old[2].copy()
    data = base.load_data(path)
    invalid[[2, 30]] = True
    scores[[2, 30]] = data["frequency_past"][[2, 30]]
    base._write_cache(old_path, metadata, scores, invalid, old[3], {"flat_scores": 2})
    result_data, results = reuse.load_scores(
        path, ["markov_lag2"], new_root, "new_protocol", workers=1, start=280
    )
    assert results["markov_lag2"][4] == {"flat_scores": 1}
    assert results["markov_lag2"][2].sum() == 1
    assert (
        result_data["score_cache_provenance"]["markov_lag2"]["mode"]
        == "reused_stateless_scores"
    )


def test_ambiguous_sliced_reasons_recompute_instead_of_inventing_counts(prepared):
    path, _, new_root, old_path, metadata, old = prepared
    invalid = old[2].copy()
    invalid[[2, 30]] = True
    base._write_cache(
        old_path,
        metadata,
        old[1],
        invalid,
        old[3],
        {"flat_scores": 1, "nonfinite_scores": 1},
    )
    data, results = reuse.load_scores(
        path, ["markov_lag2"], new_root, "new_protocol", workers=1, start=280
    )
    assert not results["markov_lag2"][2].any()
    assert results["markov_lag2"][4] == {}
    assert (
        data["score_cache_provenance"]["markov_lag2"]["mode"] == "computed_current_code"
    )


def test_conflicting_compatible_files_are_not_cherry_picked(prepared):
    path, _, new_root, old_path, metadata, old = prepared
    altered = old[1].copy()
    altered[30, 0] += 0.01
    other = old_path.with_name("scores_synthetic_markov_lag2_other.npz")
    base._write_cache(
        other, {**metadata, "identity": "another_old_protocol"}, altered, *old[2:]
    )
    data, results = reuse.load_scores(
        path, ["markov_lag2"], new_root, "new_protocol", workers=1, start=280
    )
    assert data["score_cache_provenance"]["markov_lag2"]["legacy_metadata_conflict"]
    expected = old[1][np.searchsorted(base.load_data(path)["targets"], data["targets"])]
    np.testing.assert_array_equal(results["markov_lag2"][1], expected)


def test_old_cache_cannot_be_an_output_destination(prepared):
    path, old_root, _, old_path, _, _ = prepared
    before = old_path.read_bytes()
    with pytest.raises(ValueError, match="read-only"):
        reuse.load_scores(
            path, ["markov_lag2"], old_root / "country", "new_protocol", workers=1
        )
    assert old_path.read_bytes() == before
