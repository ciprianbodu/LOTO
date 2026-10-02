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
