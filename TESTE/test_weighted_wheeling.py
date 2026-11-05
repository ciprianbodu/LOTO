"""Teste pentru Weighted / Positional Wheeling."""

from __future__ import annotations

import itertools
import pytest

from covering.common import _coverage_pct
from covering.dispatch import generate_wheel, resolve_wheel_method
from covering.weighted import (
    align_positions_by_degree,
    weighted_combinatorial_wheel,
    wheel_weighted,
)


def test_align_positions_by_degree():
    # Blocks unde pozitia 4 apare de 3 ori, iar 1, 2, 3 de 2 ori
    blocks = [[1, 2, 4], [1, 3, 4], [2, 3, 4]]
    aligned, order = align_positions_by_degree(blocks, 4)

    # Pozitia 4 originala avea gradul 3, deci trebuie sa fi devenit pozitia 1
    assert order[0] == 4
    # In blocurile aliniate, 1 trebuie sa apara de 3 ori
    flat = [x for b in aligned for x in b]
    assert flat.count(1) == 3


def test_wheel_weighted_uncapped_100pct_coverage():
    pool = list(range(1, 11))
    scores = {n: float(11 - n) for n in pool}  # 1 are scorul cel mai mare
    wheel, cov = wheel_weighted(pool, pick=5, guarantee=4, max_variants=0, scores=scores)

    assert cov == 100.0
    assert len(wheel) > 0
    # Toate biletele contin 5 numere distincte
    for b in wheel:
        assert len(b) == 5
        assert len(set(b)) == 5
        assert all(n in pool for n in b)


def test_wheel_weighted_budget_prioritizes_top_targets():
    pool = list(range(1, 15))
    scores = {n: float(15 - n) for n in pool}  # numerele 1..7 au scoruri de top
    pick = 6
    guarantee = 4
    max_variants = 10

    wheel, cov = wheel_weighted(
        pool, pick=pick, guarantee=guarantee, max_variants=max_variants, scores=scores
    )

    assert len(wheel) <= max_variants
    assert cov > 0.0

    # Fiecare numar din pool trebuie sa apara pe cel putin un bilet
    seen_nums = set(x for b in wheel for x in b)
    assert seen_nums == set(pool)

    # Verificam ca frecventa numerelor de top (1..4) este mai mare decat a celor din coada (11..14)
    top_counts = sum(b.count(n) for b in wheel for n in (1, 2, 3, 4))
    bottom_counts = sum(b.count(n) for b in wheel for n in (11, 12, 13, 14))
    assert top_counts > bottom_counts


def test_dispatch_weighted_method():
    assert resolve_wheel_method(10, requested="weighted") == "weighted"

    pool = list(range(1, 11))
    scores = {n: float(11 - n) for n in pool}
    wheel, cov = generate_wheel(
        "weighted", pool, pick=5, guarantee=3, max_variants=5, scores=scores
    )
    assert len(wheel) == 5
    assert cov > 0.0


@pytest.mark.parametrize("invalid", [None, "bad", float("nan"), float("inf"), -float("inf")])
def test_weighted_handles_invalid_scores(invalid):
    from covering.weighted import _calculate_weights
    scores = {1: invalid, 2: 1.0, 3: 2.0}
    weights = _calculate_weights([1, 2, 3], scores)
    assert weights == {1: 1.0, 2: 1.0, 3: 3.0}
    pool = list(range(1, 10))
    wheel, coverage = wheel_weighted(pool, 5, 3, 3, scores)
    assert coverage == _coverage_pct(wheel, pool, 3)


def test_weighted_extreme_finite_scores_keep_middle_weight():
    from covering.weighted import _calculate_weights
    assert _calculate_weights([1, 2, 3], {1: -1e308, 2: 0., 3: 1e308}) == {1: 1., 2: 2., 3: 3.}


def test_weighted_missing_scores_are_not_an_artificial_zero_extreme():
    from covering.weighted import _calculate_weights
    assert _calculate_weights([1, 2, 3, 4], {1: 10., 2: 20., 3: 15.}) == {1: 1., 2: 3., 3: 2., 4: 1.}


def test_degree_relabeling_preserves_exact_uniform_hit_profile():
    from covering.probability import wheel_hit_profile
    blocks = [[1, 2, 4], [1, 3, 4], [2, 3, 4]]
    before = [row[:] for row in blocks]
    aligned, _ = align_positions_by_degree(blocks, 4)
    assert blocks == before
    assert wheel_hit_profile([1, 2, 3, 4], aligned) == wheel_hit_profile([1, 2, 3, 4], blocks)


@pytest.mark.parametrize("kwargs", [{"pick": 0}, {"guarantee": 0}, {"guarantee": 7}, {"max_variants": 1.5}])
@pytest.mark.parametrize("generator", [wheel_weighted, weighted_combinatorial_wheel])
def test_weighted_rejects_invalid_geometry(kwargs, generator):
    with pytest.raises(ValueError):
        generator(list(range(1, 10)), **{"pick": 6, "guarantee": 4, **kwargs})


def test_weighted_rejects_duplicate_pool():
    with pytest.raises(ValueError):
        wheel_weighted([1, 2, 3, 4, 5, 5], 5, 3)
