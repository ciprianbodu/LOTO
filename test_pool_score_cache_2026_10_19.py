"""Research caches preserve strict-day prefixes and expose score fallback."""

import hashlib

import numpy as np
import pandas as pd
import pytest

from scripts.analysis import pool_score_cache_2026_10_19 as cache
from loto_enterprise.benchmark.methods import call_method


@pytest.fixture
def history(tmp_path):
    rng = np.random.default_rng(20261019)
    draws = np.array([rng.choice(49, 6, replace=False) + 1 for _ in range(30)])
    dates = pd.date_range("2020-01-01", periods=30).strftime("%d-%m-%Y").tolist()
    dates[16] = dates[15]
    frame = pd.DataFrame(draws, columns=[f"n{i}" for i in range(1, 7)])
    frame.insert(0, "date", dates)
    path = tmp_path / "synthetic.csv"
    frame.to_csv(path, index=False)
    return path


def _args(history, tmp_path, **extra):
    return {
        "path": str(history),
        "name": "frequency",
        "start": 10,
        "cache_dir": str(tmp_path / "scores"),
        "identity": "frozen",
        **extra,
    }


def test_load_data_preserves_same_day_cutoffs_and_real_frequency(history):
    raw = history.read_bytes()
    data = cache.load_data(history, start=10)
    assert data["dates"][15] == data["dates"][16] == "2020-01-16"
    assert data["prior"][15] == data["prior"][16] == 15
    assert data["groups"][15] == data["groups"][16]
    assert data["source_sha"] == hashlib.sha256(raw).hexdigest()
    for row in (0, 5, 6, 10):
        target = data["targets"][row]
        prior = int(data["prior"][target])
        expected, _ = call_method("frequency", data["draws"][:prior], 49)
        np.testing.assert_array_equal(
            data["frequency_past"][row], [expected[n] for n in range(1, 50)]
        )
    assert all(
        not data[key].flags.writeable
        for key in ("dates", "draws", "prior", "groups", "targets", "frequency_past")
    )
    assert history.read_bytes() == raw


def test_cache_hit_does_not_rescore_and_identity_change_recalculates(
    history, tmp_path, monkeypatch
):
    args = _args(history, tmp_path)
    original = cache.evaluate_method(args)
    data = cache.load_data(history, start=10)
    calls = []

    def observed(name, prefix, maximum, **kwargs):
        calls.append(len(prefix))
        assert not prefix.flags.writeable
        np.testing.assert_array_equal(
            kwargs["history_cutoffs"], data["prior"][: len(prefix)]
        )
        return call_method(name, prefix, maximum, **kwargs)

    monkeypatch.setattr(cache, "call_method", observed)
    hit = cache.evaluate_method(args)
    assert calls == []
    np.testing.assert_array_equal(hit[1], original[1])
    np.testing.assert_array_equal(hit[2], original[2])
    assert hit[3:] == original[3:]
    changed = cache.evaluate_method({**args, "identity": "new frozen identity"})
    assert calls == list(dict.fromkeys(data["prior"][data["targets"]].tolist()))
    np.testing.assert_array_equal(changed[1], data["frequency_past"])
    assert not changed[2].any()
    assert changed[4] == {}
    assert len(list((tmp_path / "scores").glob("*.npz"))) == 2
    assert not list((tmp_path / "scores").glob("*.tmp"))


@pytest.mark.parametrize("kind", ["incomplete", "flat", "nonfinite", "exception"])
def test_invalid_outputs_are_visible_with_canonical_frequency_fallback(
    history, tmp_path, monkeypatch, kind
):
    data = cache.load_data(history, start=10)

    def faulty(name, prefix, maximum, **kwargs):
        if name == "frequency":
            return call_method(name, prefix, maximum, **kwargs)
        if kind == "exception":
            raise ArithmeticError("synthetic failure")
        values = {n: float(n) for n in range(1, 50)}
        if kind == "incomplete":
            values.pop(49)
        elif kind == "flat":
            values = dict.fromkeys(range(1, 50), 0.0)
        else:
            values[49] = float("nan")
        return values, 0.0

    monkeypatch.setattr(cache, "call_method", faulty)
    name, scores, invalid, elapsed, errors = cache.evaluate_method(
        _args(history, tmp_path, name=f"faulty_{kind}")
    )
    np.testing.assert_array_equal(scores, data["frequency_past"])
    assert invalid.all()
    assert sum(errors.values()) == len(data["targets"])
    assert len(errors) == 1
    assert elapsed >= 0
    if kind == "incomplete":
        assert errors == {"incomplete_scores": len(invalid)}


def test_corrupt_npz_is_rebuilt_without_touching_input(history, tmp_path):
    args = _args(history, tmp_path)
    expected = cache.evaluate_method(args)
    file = next((tmp_path / "scores").glob("*.npz"))
    file.write_bytes(b"PK\x03\x04broken zip")
    actual = cache.evaluate_method(args)
    np.testing.assert_array_equal(actual[1], expected[1])
    np.testing.assert_array_equal(actual[2], expected[2])
    assert actual[4] == expected[4]


def test_changed_source_invalidates_cache(history, tmp_path):
    args = _args(history, tmp_path)
    old = cache.evaluate_method(args)
    frame = pd.read_csv(history)
    frame.loc[len(frame)] = ["31-01-2020", 1, 2, 3, 4, 5, 6]
    frame.to_csv(history, index=False)
    new = cache.evaluate_method(args)
    assert len(new[1]) == len(old[1]) + 1
    assert len(list((tmp_path / "scores").glob("*.npz"))) == 2


def test_load_rejects_invalid_draw_instead_of_realigning_silently(history):
    frame = pd.read_csv(history)
    frame.loc[0, "n6"] = frame.loc[0, "n5"]
    frame.to_csv(history, index=False)
    with pytest.raises(ValueError, match="invalid 6/49"):
        cache.load_data(history, start=10)
