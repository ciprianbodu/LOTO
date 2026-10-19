"""Protocol and signed artifacts of the new combination study."""

import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

SPEC = importlib.util.spec_from_file_location(
    "combination_runner",
    Path(__file__).resolve().parent
    / "scripts/analysis/pool_combinations_649_2026_10_19.py",
)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_signed_json_is_lf_and_preserves_unicode(tmp_path):
    path = tmp_path / "protocol.json"
    module.write_json(path, {"test": "număr"})
    assert b"\r" not in path.read_bytes()
    assert json.loads(path.read_bytes()) == {"test": "număr"}


def test_descriptors_match_expert_bank_and_two_controls():
    specs = module.specs()
    expected = 4 * (len(module.methods()) - 1) + 24 + 2
    assert len(specs) == expected
    assert specs[0]["name"] == "markov_lag2"
    assert specs[1]["name"] == "frequency"
    assert len({s["name"] for s in specs}) == expected
    assert all(s["complexity"] == len(set(s["parent_names"])) for s in specs)


def test_primary_metrics_keep_8_pool_sizes_inside_each_draw():
    hits = np.zeros((2, 8), dtype=int)
    hits[0, 0] = 4
    result = module.metrics(hits)
    assert result["n"] == 2
    assert result["primary"] == 1 / 16
    assert result["events4"] == [1, 0, 0, 0, 0, 0, 0, 0]


def test_empty_segment_cannot_be_reported_as_zero_performance():
    with pytest.raises(ValueError):
        module.metrics(np.zeros((0, 8)))
