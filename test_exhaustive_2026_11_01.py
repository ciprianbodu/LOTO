"""Enumerarea exhaustivă (covering/exhaustive.py) trebuie să fie exactă pe
geometrii mici (identică cu numărătoarea combinatorială), să refuze geometrii
invalide și să ruleze și fără cupy (producția e CPU-only)."""

from math import comb

import pytest

from covering.exhaustive import (
    enumerate_hit_histogram,
    gpu_available,
    verify_wheel_exact,
)
from covering.probability import wheel_hit_probabilities


def test_enumeration_matches_combinatorial_small():
    # C(10,3) = 120 extrageri, wheel cu 4 bilete pe pool 1..10
    pool = list(range(1, 11))
    tickets = [(1, 2, 3), (4, 5, 6), (7, 8, 9), (1, 4, 7)]
    r = enumerate_hit_histogram(pool, tickets, draw_n=3, max_num=10, batch=50)
    assert r["total_draws"] == comb(10, 3) == 120
    assert sum(r["counts"]) == 120
    assert len(r["counts"]) == 4  # h = 0..3
    exact = wheel_hit_probabilities(pool, tickets, 3, 10)["ticket"]
    for t in (1, 2, 3):
        assert abs(r["ticket_prob_ge"][t] - exact[t]) < 1e-12
    assert r["backend"] in ("numpy", "cupy")


def test_enumeration_complete_cover_histogram():
    # sistem complet C(6,3): toate cele 20 de bilete → fiecare extragere are max-hit 3
    pool = list(range(1, 7))
    from itertools import combinations

    tickets = list(combinations(pool, 3))
    r = enumerate_hit_histogram(pool, tickets, draw_n=3, max_num=8, batch=1000)
    # doar cele C(6,3)=20 de extrageri cu toate cele 3 numere în pool au hit 3
    assert r["counts"][3] == comb(6, 3) == 20
    assert sum(r["counts"]) == comb(8, 3) == 56
    assert r["ticket_prob_ge"][3] == pytest.approx(20 / 56)


def test_verify_wheel_exact_ok():
    pool = list(range(1, 13))
    tickets = [(1, 2, 3, 4, 5, 6), (1, 2, 7, 8, 9, 10), (3, 4, 7, 8, 11, 12)]
    v = verify_wheel_exact(pool, tickets, draw_n=6, max_num=49, use_gpu=False)
    assert v["ok"] and v["max_abs_diff"] == 0.0
    assert v["total_draws"] == comb(49, 6)
    assert v["backend"] == "numpy"


def test_invalid_geometry_rejected():
    with pytest.raises(ValueError):
        enumerate_hit_histogram([1, 2, 3], [(1, 2, 3)], draw_n=7, max_num=6)
    with pytest.raises(ValueError):
        enumerate_hit_histogram([1, 2, 50], [(1, 2, 50)], draw_n=6, max_num=49)


def test_gpu_available_is_bool_and_cpu_fallback():
    assert isinstance(gpu_available(), bool)
    # în venv-ul de producție (fără cupy) backendul explicit CPU trebuie să fie numpy
    r = enumerate_hit_histogram([1, 2, 3, 4], [(1, 2, 3), (2, 3, 4)], 3, 6, use_gpu=False)
    assert r["backend"] == "numpy"
