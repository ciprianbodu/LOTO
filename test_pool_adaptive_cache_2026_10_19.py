"""Adaptive research reuses causal matrices without changing old artifacts."""

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts.analysis import pool_adaptive_cache_2026_10_19 as adaptive
from scripts.analysis import pool_score_cache_2026_10_19 as base


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    rng = np.random.default_rng(20261019)
    frame = pd.DataFrame(
        [rng.choice(49, 6, replace=False) + 1 for _ in range(300)],
        columns=[f"n{i}" for i in range(1, 7)],
    )
    dates = pd.date_range("2020-01-01", periods=300).strftime("%d-%m-%Y").tolist()
    dates[281] = dates[280]
    frame.insert(0, "date", dates)
    path = tmp_path / "synthetic.csv"
    frame.to_csv(path, index=False)
    combination, research, output = (tmp_path / n for n in ("old_comb", "old", "new"))
    monkeypatch.setattr(adaptive, "SOURCE_CACHE_ROOTS", (combination, research))
    monkeypatch.setattr(adaptive, "ADAPTIVE_CACHE_ROOT", output)
    data = base.load_data(path, start=256)
    scores = np.tile(np.arange(49, dtype=np.float64), (len(data["targets"]), 1))
    scores += np.arange(len(scores))[:, None] / 100
    result = ("markov_lag2", scores, np.zeros(len(scores), dtype=bool), 1.25, {})
    metadata = adaptive._reuse._metadata(data, result[0], "old_manifest")
    old_path = base._cache_file(combination, path, result[0], metadata)
    base._write_cache(old_path, metadata, *result[1:])

    def forbidden(*args, **kwargs):
        raise AssertionError("no production scorer may run in these tests")

    monkeypatch.setattr(base, "evaluate_method", forbidden)
    return path, combination, research, output, old_path, metadata, result


def test_reuses_exact_slice_and_signs_provenance_without_old_writes(prepared):
    path, _, research, output, old_path, metadata, old = prepared
    before = old_path.read_bytes()
    duplicate = research / old_path.name
    base._write_cache(duplicate, {**metadata, "identity": "another_manifest"}, *old[1:])
    duplicate_before = duplicate.read_bytes()
    original = base.load_data(path, start=256)
    data, results = adaptive.load_scores(
        path, [old[0]], output / "country", "new_manifest", workers=1, start=280
    )
    positions = np.searchsorted(original["targets"], data["targets"])
    np.testing.assert_array_equal(original["targets"][positions], data["targets"])
    assert data["prior"][data["targets"][0]] == 280
    assert data["targets"][0] == 280
    assert data["targets"][1] == 281  # Both draws on one date see the same prefix.
    np.testing.assert_array_equal(results[old[0]][1], old[1][positions])
    assert results[old[0]][3:] == old[3:]
    assert old_path.read_bytes() == before
    assert duplicate.read_bytes() == duplicate_before
    report = data["score_cache_provenance"][old[0]]
    assert report["equivalent_compatible_cache_count"] == 2
    assert report["source_cache_sha256"] == hashlib.sha256(before).hexdigest()
    assert report["aligned_target_sha256"] == adaptive._aligned_target_sha(data)
    assert report["slice_positions"] == positions.tolist()
    assert report["target_metadata"]["identity"] == "new_manifest"
    payload = {k: v for k, v in report.items() if k != "provenance_sha256"}
    assert report["provenance_sha256"] == adaptive._canonical_sha(payload)
    again, cached = adaptive.load_scores(
        path, [old[0]], output / "country", "new_manifest", workers=1, start=280
    )
    np.testing.assert_array_equal(cached[old[0]][1], results[old[0]][1])
    assert again["score_cache_provenance"] == data["score_cache_provenance"]


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
def test_missing_or_incompatible_is_hard_error(prepared, field, value):
    path, _, _, output, old_path, metadata, old = prepared
    base._write_cache(old_path, {**metadata, field: value}, *old[1:])
    before = old_path.read_bytes()
    with pytest.raises(
        RuntimeError, match="required compatible raw-score cache missing"
    ):
        adaptive.load_scores(path, [old[0]], output, "new", workers=1, start=280)
    assert old_path.read_bytes() == before
    assert not output.exists()


def test_conflicting_duplicates_fail_instead_of_selecting_a_source(prepared):
    path, _, research, output, old_path, metadata, old = prepared
    changed = old[1].copy()
    changed[30, 0] += 0.1
    base._write_cache(research / old_path.name, metadata, changed, *old[2:])
    with pytest.raises(RuntimeError, match="conflicting compatible"):
        adaptive.load_scores(path, [old[0]], output, "new", workers=1, start=280)
    assert not output.exists()


@pytest.mark.parametrize("kind", ["source", "outside"])
def test_old_or_unapproved_output_paths_are_rejected(prepared, kind):
    path, combination, _, output, _, _, old = prepared
    bad = combination / "nested" if kind == "source" else output.parent / "unapproved"
    with pytest.raises(ValueError):
        adaptive.load_scores(path, [old[0]], bad, "new", workers=1)
    assert not bad.exists()


def test_corrupt_current_provenance_is_rebuilt_from_unchanged_source(prepared):
    path, _, _, output, old_path, _, old = prepared
    data, results = adaptive.load_scores(path, [old[0]], output, "new", workers=1)
    sidecar = next(output.glob("*.provenance.json"))
    report = json.loads(sidecar.read_text())
    report["aligned_target_sha256"] = "wrong"
    sidecar.write_text(json.dumps(report), encoding="utf-8")
    before = old_path.read_bytes()
    again, cached = adaptive.load_scores(path, [old[0]], output, "new", workers=1)
    np.testing.assert_array_equal(cached[old[0]][1], results[old[0]][1])
    assert again["score_cache_provenance"] == data["score_cache_provenance"]
    assert old_path.read_bytes() == before


def test_recomputed_cache_cannot_satisfy_later_require_reuse(prepared, monkeypatch):
    path, _, _, output, old_path, _, old = prepared
    old_path.unlink()
    calls = []

    def synthetic_evaluator(args):
        calls.append(args["name"])
        data = base.load_data(args["path"], start=args["start"])
        metadata = adaptive._reuse._metadata(data, args["name"], args["identity"])
        target = base._cache_file(args["cache_dir"], path, args["name"], metadata)
        base._write_cache(target, metadata, *old[1:])
        return old

    monkeypatch.setattr(base, "evaluate_method", synthetic_evaluator)
    data, results = adaptive.load_scores(
        path, [old[0]], output, "new", workers=1, require_reuse=False
    )
    assert calls == [old[0]]
    assert data["score_cache_provenance"][old[0]]["mode"] == "computed_current_code"
    with pytest.raises(RuntimeError, match="required compatible"):
        adaptive.load_scores(path, [old[0]], output, "new", workers=1)
    assert calls == [old[0]]
    np.testing.assert_array_equal(results[old[0]][1], old[1])


def test_invalid_fallback_mask_and_error_counts_are_preserved(prepared):
    path, _, _, output, old_path, metadata, old = prepared
    invalid = old[2].copy()
    invalid[[2, 30]] = True
    base._write_cache(old_path, metadata, old[1], invalid, old[3], {"flat_scores": 2})
    _, results = adaptive.load_scores(
        path, [old[0]], output, "new", workers=1, start=280
    )
    assert results[old[0]][2].sum() == 1
    assert results[old[0]][4] == {"flat_scores": 1}


def test_default_destination_is_dedicated_temp_directory(monkeypatch):
    expected = (
        Path(adaptive.tempfile.gettempdir()) / "loto-pool-adaptive-20261019" / "cache"
    )
    monkeypatch.setattr(adaptive, "ADAPTIVE_CACHE_ROOT", expected)
    assert adaptive._destination(None) == expected.resolve()
