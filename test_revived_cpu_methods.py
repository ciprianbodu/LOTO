"""CPU tombstones restored into METHODS."""

import numpy as np

from loto_enterprise.benchmark.disabled import load_disabled
from loto_enterprise.benchmark.methods import METHODS
from loto_enterprise.benchmark.methods_revived import REVIVED_METHODS


def test_revived_names_are_registered_and_cpu():
    assert load_disabled() == {"ml_gaussian_process"}
    for name in REVIVED_METHODS:
        assert name in METHODS, name
    assert "omnius" not in METHODS
    assert "omnius" not in load_disabled()


def test_cheap_revived_scorers_return_full_universe():
    draws = np.array([[1, 2, 3, 4, 5, 6], [2, 3, 4, 5, 6, 7], [7, 8, 9, 1, 2, 3]], dtype=int)
    for name in ("recency", "markov_1", "weighted_recent", "polya_urn"):
        out = METHODS[name][0](draws, 10)
        assert set(out) == set(range(1, 11))
        assert all(v == v for v in out.values())
