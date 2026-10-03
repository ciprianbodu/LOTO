"""Experimentul preinregistrat 2026-10-02 (hituri in pool): amprentele datelor
si parametrii nu se pot schimba dupa inregistrare."""

import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent
HERE = ROOT / "scripts" / "analysis" / "pool_hit_experiment"
REG = json.loads((HERE / "preregistration_2026-10-02.json").read_text(encoding="utf-8"))

_spec = importlib.util.spec_from_file_location("pool_hit_experiment", HERE / "pool_hit_experiment.py")
phe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(phe)


@pytest.mark.parametrize("name", sorted(REG["datasets"]))
def test_registered_prefix_of_each_file_is_unchanged(name):
    info = REG["datasets"][name]
    assert phe.DATASETS[name][0] == info["file"]
    assert phe.prefix_hash(ROOT / info["file"], info["rows"]) == info["prefix_sha256"]


def test_prefix_hash_ignores_crlf_and_appended_rows(tmp_path):
    lf = tmp_path / "a.csv"
    lf.write_bytes(b"date,n1\n01-01-2000,1\n02-01-2000,2\n")
    crlf = tmp_path / "b.csv"
    crlf.write_bytes(b"date,n1\r\n01-01-2000,1\r\n02-01-2000,2\r\n03-01-2000,3\r\n")
    assert phe.prefix_hash(lf, 2) == phe.prefix_hash(crlf, 2)


def test_code_parameters_are_the_registered_ones():
    assert phe.WARMUP == REG["warmup"]
    assert phe.META_WINDOW == REG["meta_window"]
    assert phe.META_TOP == REG["meta_top"]
    assert phe.DEV_FRACTION == REG["dev_fraction"]
    assert phe.ALPHA == REG["alpha"]
    assert list(phe.CANDIDATES) == REG["candidates"]
    assert [[c, k, t] for c, _s, k, t in phe.TEST_CELLS] == REG["cells"]
    assert phe.production_methods() == REG["methods"]


def test_hypergeometric_baselines():
    assert phe.baseline(49, 6, 6, 3) == pytest.approx(0.018637, abs=1e-5)
    assert phe.baseline(49, 6, 12, 3) == pytest.approx(0.147980, abs=1e-5)


def test_holm_is_monotone_and_capped():
    adj = phe.holm({"a": 0.01, "b": 0.04, "c": 0.03})
    assert adj == {"a": 0.03, "c": 0.06, "b": 0.06}


def test_borda_ties_go_to_the_larger_number():
    rows = np.array([[1, 2, 3], [2, 1, 3]], dtype=np.int16)
    assert list(phe.borda(rows, 3)) == [2, 1, 3]


@pytest.mark.parametrize(
    "scores",
    [
        {},
        {1: 1.0},
        {1: 1.0, 2: 1.0, 3: 1.0},
        {1: 1.0, 2: 1.0 + 1e-14, 3: 1.0},
        {1: 1.0, 2: float("nan"), 3: 3.0},
        {1: 1.0, 2: float("inf"), 3: 3.0},
        {1: 1.0, 2: "not a score", 3: 3.0},
    ],
)
def test_ranking_rejects_scores_unusable_in_production(scores):
    with pytest.raises(ValueError, match="scor inutilizabil"):
        phe.ranking(scores, 3)


def test_ranking_keeps_canonical_ties_for_usable_scores():
    assert phe.ranking({1: 3.0, 2: 3.0, 3: 0.0}, 3).tolist() == [2, 1, 3]


@pytest.mark.parametrize("raises", [False, True])
def test_dataset_counts_fallback_and_excludes_same_day(monkeypatch, raises):
    from loto_enterprise.benchmark.methods import METHODS

    dates = np.array(
        ["2000-01-01", "2000-01-02", "2000-01-02", "2000-01-03"],
        dtype="datetime64[D]",
    )
    draws = np.array([[1], [2], [3], [1]])
    histories = []

    def bad(history, max_num):
        histories.append(history.copy())
        if raises:
            raise RuntimeError("simulated scorer failure")
        return {1: 1.0, 2: 1.0, 3: 1.0}

    def frequency(history, max_num):
        return {1: 3.0, 2: 2.0, 3: 1.0}

    monkeypatch.setattr(phe, "WARMUP", 1)
    monkeypatch.setattr(phe, "production_methods", lambda: ["bad", "frequency"])
    monkeypatch.setattr(phe, "load", lambda *_: (dates, draws, 3, 1))
    monkeypatch.setitem(METHODS, "bad", (bad, {}))
    monkeypatch.setitem(METHODS, "frequency", (frequency, {}))
    result = phe._evaluate_dataset("fixture", 4)

    assert result["targets"] == [1, 2, 3]
    assert [h.ravel().tolist() for h in histories] == [[1], [1], [1, 2, 3]]
    assert result["fallback_counts"] == {"bad": 3, "frequency": 0}
    assert np.array_equal(result["ranks"][:, 0], result["ranks"][:, 1])
    assert result["ranks"][0, 0].tolist() == [1, 2, 3]


def test_checkpoint_identity_tracks_code_registry_and_registration(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(phe, "ROOT", tmp_path)
    monkeypatch.setattr(phe, "production_methods", lambda: ["frequency"])
    source = tmp_path / "loto_enterprise" / "benchmark" / "methods_example.py"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"def score():\n    return 1\n")
    reg = {"methods": ["frequency"], "datasets": {"fixture": {"prefix_sha256": "abc"}}}
    original = phe.checkpoint_identity(reg)
    assert phe.checkpoint_identity(reg) == original
    source.write_bytes(b"def score():\r\n    return 1\r\n")
    assert phe.checkpoint_identity(reg) == original
    source.write_bytes(b"def score():\n    return 2\n")
    assert phe.checkpoint_identity(reg) != original
    source.write_bytes(b"def score():\n    return 1\n")
    monkeypatch.setattr(phe, "production_methods", lambda: ["frequency", "new_method"])
    assert phe.checkpoint_identity(reg) != original
    monkeypatch.setattr(phe, "production_methods", lambda: ["frequency"])
    changed_reg = {**reg, "datasets": {"fixture": {"prefix_sha256": "changed"}}}
    assert phe.checkpoint_identity(changed_reg) != original
    monkeypatch.setattr(phe, "CHECKPOINT_VERSION", phe.CHECKPOINT_VERSION + 1)
    assert phe.checkpoint_identity(reg) != original


def test_checkpoint_rejects_old_and_mismatched_results(tmp_path):
    import pickle

    path = tmp_path / "evaluation.pkl"
    evaluation = {
        "name": "fixture",
        "fallback_counts": {"frequency": 0},
        "ranks": np.array([[[3, 2, 1]]], dtype=np.int16),
    }
    assert phe._read_checkpoint(path, "current") is None
    path.write_bytes(pickle.dumps(evaluation))
    assert phe._read_checkpoint(path, "current") is None
    phe._write_checkpoint(path, "current", evaluation)
    restored = phe._read_checkpoint(path, "current")
    assert restored["fallback_counts"] == {"frequency": 0}
    assert np.array_equal(restored["ranks"], evaluation["ranks"])
    assert phe._read_checkpoint(path, "changed-code") is None
    assert list(tmp_path.iterdir()) == [path]
    path.write_bytes(b"corrupt checkpoint")
    assert phe._read_checkpoint(path, "current") is None


def test_new_runs_default_to_separate_output(monkeypatch, tmp_path):
    original = tmp_path / "results_2026-10-02.json"
    original.write_text("original published results", encoding="utf-8")
    monkeypatch.setattr(phe, "HERE", tmp_path)
    monkeypatch.setattr(
        phe, "run", lambda jobs: {"test": {}, "audit": {"validated": True}}
    )
    monkeypatch.setattr(
        phe.sys, "argv", ["pool_hit_experiment.py", "run", "--jobs", "1"]
    )
    phe.main()
    assert original.read_text(encoding="utf-8") == "original published results"
    updated = json.loads(
        (tmp_path / "results_validated.json").read_text(encoding="utf-8")
    )
    assert updated["audit"]["validated"] is True


def test_run_refuses_registry_drift_before_evaluation(monkeypatch):
    monkeypatch.setattr(phe, "production_methods", lambda: ["frequency"])
    with pytest.raises(SystemExit, match="registry-ul difera"):
        phe.run(1)
