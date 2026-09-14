"""Characterization against main@67effec, captured BEFORE optimization.

The golden hashes include every ticket, its order, and reported coverage.
They protect score ties and budget repair as well as the combinatorial result.
"""

import hashlib
import itertools
import json
from pathlib import Path

import pytest

from loto_engine import generate_combinatorial_wheel
from wheeling_methods import compute_coverage_pct, ensure_pool_numbers_on_tickets


CASES = json.loads(Path(__file__).with_name("test_wheel_golden.json").read_text())


@pytest.mark.parametrize("v,pick,g,budget,mode,expected", CASES)
def test_wheel_matches_pre_optimization(v, pick, g, budget, mode, expected):
    pool = [3 * n - 1 for n in range(1, v + 1)][::-1]
    scores = (
        None
        if mode == "none"
        else {n: float(n % 3 if mode == "ties" else (n * 17) % 23) for n in pool}
    )
    result = generate_combinatorial_wheel(pool, pick, g, budget, scores)
    digest = hashlib.sha256(
        json.dumps(result, separators=(",", ":")).encode()
    ).hexdigest()
    assert digest == expected
    wheel, coverage = result
    assert compute_coverage_pct(wheel, pool, g) == coverage
    assert all(len(t) == len(set(t)) == pick for t in wheel)
    if budget:
        assert len(wheel) <= budget


def test_capped_full_system_only_materializes_requested_tickets(monkeypatch):
    """A small budget must not allocate C(pool, pick) discarded tickets."""
    original = itertools.combinations
    visited = 0

    def counted(pool, pick):
        nonlocal visited
        for ticket in original(pool, pick):
            visited += 1
            yield ticket

    monkeypatch.setattr(itertools, "combinations", counted)
    wheel, coverage = generate_combinatorial_wheel(
        list(range(1, 17)), pick=6, guarantee=6, max_variants=7
    )
    assert len(wheel) == visited == 7
    assert coverage < 100


def test_repair_tracks_swaps_without_losing_previously_present_numbers():
    wheel = [[1, 2, 3], [1, 2, 3], [1, 2, 3]]
    repaired = ensure_pool_numbers_on_tickets(wheel, list(range(1, 10)), pick=3)
    assert repaired == [[1, 2, 3], [7, 8, 9], [4, 5, 6]]
    assert wheel == [[1, 2, 3], [1, 2, 3], [1, 2, 3]]
    assert set().union(*map(set, repaired)) == set(range(1, 10))
