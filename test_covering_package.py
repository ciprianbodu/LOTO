"""Covering package split: public wheeling_methods API still resolves."""

from covering.greedy import generate_combinatorial_wheel as greedy
from covering.designs import wheel_lajolla, wheel_lotto, wheel_union34
from wheeling_methods import (
    WHEEL_METHODS,
    compute_coverage_pct,
    generate_wheel,
)


def test_greedy_full_cover_small_pool():
    wheel, cov = greedy(list(range(1, 9)), pick=5, guarantee=3, max_variants=0)
    assert wheel
    assert cov == 100.0
    assert compute_coverage_pct(wheel, list(range(1, 9)), 3) == 100.0


def test_generate_wheel_greedy_matches_covering():
    args = (list(range(1, 9)), 5, 3, 0, None)
    a, ca = greedy(*args)
    b, cb = generate_wheel("greedy", *args)
    assert ca == cb
    assert a == b


def test_public_methods_registered():
    assert {"ilp", "lajolla", "genetic", "annealing", "union34", "maxcover"} <= set(
        WHEEL_METHODS
    )


def test_lotto_condition_equals_guarantee_does_not_crash():
    wheel, cov = wheel_lotto(list(range(1, 9)), 5, 3, 3, max_variants=0)
    assert wheel
    assert cov == 100.0


def test_union34_and_capped_lajolla_do_not_crash():
    pool = list(range(1, 11))
    u, cu = wheel_union34(pool, 5, 3, max_variants=0)
    assert u
    assert cu >= 0.0
    w, c = wheel_lajolla(pool, 5, 3, max_variants=3)
    assert w
    assert c >= 0.0


def test_union34_pick_below_four_does_not_request_invalid_g4():
    wheel, cov = wheel_union34(list(range(1, 8)), 3, 3, max_variants=0)
    assert wheel
    assert cov >= 0.0
