"""Synthetic geometry, causality, fallback and cache-recovery regressions."""

import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from scripts.analysis import pool_extension_cache_2026_10_19 as cache


@pytest.fixture(params=[(40, 6), (45, 5)], ids=["540_six_drawn", "joker_first_urn"])
def prepared(request, tmp_path, monkeypatch):
    max_num, draw_n = request.param
    rng = np.random.default_rng(6101940 + max_num)
    draws = [rng.choice(max_num, draw_n, replace=False) + 1 for _ in range(22)]
    frame = pd.DataFrame(draws, columns=[f"n{i}" for i in range(1, draw_n + 1)])
    dates = (
        pd.date_range("2020-01-01", periods=len(frame)).strftime("%d-%m-%Y").tolist()
    )
    dates[8] = dates[7]
    frame.insert(0, "date", dates)
    frame["joker"] = 20  # The optional second urn is never part of draw_n.
    frame = frame.sample(frac=1, random_state=9).reset_index(drop=True)
    path = tmp_path / "synthetic.csv"
    frame.to_csv(path, index=False)
    root = tmp_path / "approved_temp_cache"
    monkeypatch.setattr(cache, "CACHE_ROOT", root)
    cache._DATA_CACHE.clear()
    return path, root, max_num, draw_n


def load(prepared, **kwargs):
    path, _, max_num, draw_n = prepared
    return cache.load_data(path, max_num=max_num, draw_n=draw_n, start=6, **kwargs)


def run(prepared, names=("markov_lag2",), identity="new_manifest"):
    path, root, max_num, draw_n = prepared
    return cache.load_scores(
        path,
        names,
        root,
        identity,
        max_num=max_num,
        draw_n=draw_n,
        workers=1,
        start=6,
    )


def test_chronology_strict_prefixes_geometry_and_canonical_frequency(prepared):
    path, _, max_num, draw_n = prepared
    data = load(prepared)
    frame = pd.read_csv(path)
    order = np.argsort(pd.to_datetime(frame.date, format="%d-%m-%Y"), kind="stable")
    expected = frame.iloc[order][[f"n{i}" for i in range(1, draw_n + 1)]].to_numpy()
    np.testing.assert_array_equal(data["draws"], expected)
    assert data["draws"].shape == (22, draw_n)
    assert data["frequency_past"].shape == (len(data["targets"]), max_num)
    assert data["prior"][7] == data["prior"][8] == 7
    assert data["groups"][7] == data["groups"][8]
    for row, target in enumerate(data["targets"]):
        cutoff = data["prior"][target]
        assert (data["dates"][:cutoff] < data["dates"][target]).all()
        raw, _ = cache.call_method(
            "frequency",
            expected[:cutoff],
            max_num,
            history_cutoffs=data["prior"][:cutoff],
        )
        np.testing.assert_array_equal(
            data["frequency_past"][row], [raw[n] for n in range(1, max_num + 1)]
        )
    assert data["source_sha"] == hashlib.sha256(path.read_bytes()).hexdigest()
    for value in data.values():
        if isinstance(value, np.ndarray):
            assert not value.flags.writeable


def test_calls_once_per_day_with_only_earlier_history_and_supported_context(
    prepared, monkeypatch
):
    data = load(prepared)
    original = cache.call_method
    seen = []

    def observed(name, draws, max_num, *, history_cutoffs):
        if name == "frequency":
            return original(name, draws, max_num, history_cutoffs=history_cutoffs)
        cutoff = len(draws)
        seen.append(cutoff)
        np.testing.assert_array_equal(draws, data["draws"][:cutoff])
        np.testing.assert_array_equal(history_cutoffs, data["prior"][:cutoff])
        assert draws.shape[1] == data["draw_n"]
        return {n: n / max_num for n in range(1, max_num + 1)}, 0

    monkeypatch.setattr(cache, "call_method", observed)
    result_data, results = run(prepared, names=("frequency", "markov_lag2"))
    assert seen == sorted(set(data["prior"][data["targets"]]))
    assert list(results) == ["frequency", "markov_lag2"]
    np.testing.assert_array_equal(results["frequency"][1], data["frequency_past"])
    assert not results["markov_lag2"][2].any()
    assert result_data["aligned_target_sha256"] == data["aligned_target_sha256"]


@pytest.mark.parametrize("kind", ["empty", "flat", "nonfinite", "exception"])
def test_unusable_method_is_visible_exact_frequency_fallback(
    prepared, monkeypatch, kind
):
    data = load(prepared)
    original = cache.call_method

    def unusable(name, draws, max_num, *, history_cutoffs):
        if name == "frequency":
            return original(name, draws, max_num, history_cutoffs=history_cutoffs)
        if kind == "exception":
            raise RuntimeError("synthetic failure")
        if kind == "empty":
            return {}, 0
        value = 1.0 if kind == "flat" else np.nan
        return {n: value for n in range(1, max_num + 1)}, 0

    monkeypatch.setattr(cache, "call_method", unusable)
    _, results = run(prepared)
    _, scores, invalid, elapsed, errors = results["markov_lag2"]
    np.testing.assert_array_equal(scores, data["frequency_past"])
    assert invalid.all() and sum(errors.values()) == len(data["targets"])
    assert elapsed >= 0
    expected_reason = {
        "empty": "incomplete_scores",
        "flat": "flat_scores",
        "nonfinite": "nonfinite_scores",
        "exception": "exception:RuntimeError:synthetic failure",
    }[kind]
    assert errors == {expected_reason: len(data["targets"])}
    # Same-day rows must each appear in the visible fallback count.
    assert invalid[1] and invalid[2]


def test_exact_cache_reuse_and_corrupt_cache_recovery(prepared, monkeypatch):
    _, root, max_num, draw_n = prepared
    original = cache.call_method
    calls = []

    def observed(name, draws, n, *, history_cutoffs):
        if name != "frequency":
            calls.append(len(draws))
        return original(name, draws, n, history_cutoffs=history_cutoffs)

    monkeypatch.setattr(cache, "call_method", observed)
    data, first = run(prepared)
    count = len(calls)
    _, second = run(prepared)
    assert len(calls) == count
    np.testing.assert_array_equal(first["markov_lag2"][1], second["markov_lag2"][1])
    output = next(root.glob("*.npz"))
    with np.load(output, allow_pickle=False) as archive:
        metadata = json.loads(str(archive["metadata"].item()))
    assert metadata["geometry"] == {"max_num": max_num, "draw_n": draw_n}
    assert metadata["aligned_target_sha256"] == data["aligned_target_sha256"]
    assert metadata["code_source_sha"] == cache._code_source_sha()
    output.write_bytes(b"PK\x03\x04truncated")
    _, recovered = run(prepared)
    assert len(calls) == 2 * count
    np.testing.assert_array_equal(recovered["markov_lag2"][1], first["markov_lag2"][1])
    assert not list(root.glob("*.tmp"))


@pytest.mark.parametrize(
    "field", ["geometry", "code_source_sha", "aligned_target_sha256"]
)
def test_incompatible_signed_metadata_is_never_accepted(prepared, monkeypatch, field):
    data, results = run(prepared)
    output = next(prepared[1].glob("*.npz"))
    metadata = cache._metadata(data, "markov_lag2", "new_manifest")
    metadata[field] = "incompatible"
    wrong = results["markov_lag2"][1] + 7
    cache._write_cache(output, metadata, wrong, *results["markov_lag2"][2:])
    _, again = run(prepared)
    np.testing.assert_array_equal(again["markov_lag2"][1], results["markov_lag2"][1])


def test_changed_source_or_manifest_gets_a_new_cache_identity(prepared):
    path, root, _, _ = prepared
    data, _ = run(prepared)
    run(prepared, identity="other_manifest")
    assert len(list(root.glob("*.npz"))) == 2
    path.write_bytes(path.read_bytes() + b"\n")
    changed, _ = run(prepared)
    assert changed["source_sha"] != data["source_sha"]
    assert changed["input_sha"] == data["input_sha"]
    assert len(list(root.glob("*.npz"))) == 3


def test_start_only_changes_alignment_and_targets(prepared):
    path, _, max_num, draw_n = prepared
    first = load(prepared)
    later = cache.load_data(path, max_num=max_num, draw_n=draw_n, start=12)
    assert first["source_sha"] == later["source_sha"]
    assert first["input_sha"] == later["input_sha"]
    assert first["aligned_target_sha256"] != later["aligned_target_sha256"]
    assert (later["prior"][later["targets"]] >= 12).all()


def test_no_output_can_escape_dedicated_temp_root(prepared):
    path, root, max_num, draw_n = prepared
    outside = root.parent / "outside"
    with pytest.raises(ValueError, match="dedicated extension TEMP"):
        cache.load_scores(
            path,
            ["frequency"],
            outside,
            "new",
            max_num=max_num,
            draw_n=draw_n,
            workers=1,
            start=6,
        )
    assert not outside.exists()


def test_every_extracted_ball_is_validated(prepared):
    path, _, max_num, draw_n = prepared
    frame = pd.read_csv(path)
    frame.loc[0, f"n{draw_n}"] = max_num + 1
    frame.to_csv(path, index=False)
    with pytest.raises(ValueError, match="invalid draw rows"):
        load(prepared)


def test_invalid_dates_cannot_silently_change_prefixes(prepared):
    path = prepared[0]
    frame = pd.read_csv(path)
    frame.loc[0, "date"] = "not-a-date"
    frame.to_csv(path, index=False)
    with pytest.raises(ValueError, match="date"):
        load(prepared)
