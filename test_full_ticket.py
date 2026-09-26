"""Biletul complet: 3 variante la 6/49, 4 la 5/40, 2 la Joker, din pool."""

from __future__ import annotations

from itertools import combinations

from loto_enterprise.core.full_ticket import build_full_ticket


def _cov(variants, pool, g):
    targets = list(combinations(pool, g))
    hit = sum(any(set(t) <= set(v) for v in variants) for t in targets)
    return hit / len(targets) * 100


def test_variant_counts_and_sizes_per_game():
    pool = [3, 7, 11, 19, 24, 31, 38, 40]
    for game, n, pick in (("6/49", 3, 6), ("5/40", 4, 5), ("joker", 2, 5)):
        data = {"hard_core": pool, "guarantee": 4, "hard_core_joker": [12]}
        t = build_full_ticket(game, data)
        assert t["error"] is None
        assert len(t["variants"]) == n
        for v in t["variants"]:
            main = v[:pick]
            assert len(main) == pick and set(main) <= set(pool)
        assert abs(t["coverage"] - round(_cov([v[:pick] for v in t["variants"]], pool, 4), 2)) < 0.02


def test_joker_ball_appended_to_every_variant():
    t = build_full_ticket(
        "joker", {"hard_core": [1, 6, 9, 23, 24, 29, 42, 43], "guarantee": 4, "hard_core_joker": [20]}
    )
    assert t["joker"] == 20
    assert all(len(v) == 6 and v[-1] == 20 for v in t["variants"])


def test_every_pool_number_is_played():
    pool = [1, 6, 9, 23, 24, 29, 42, 43]
    t = build_full_ticket("5/40", {"hard_core": pool, "guarantee": 4})
    assert set(pool) <= {n for v in t["variants"] for n in v}


def test_pool_smaller_than_a_ticket_is_refused():
    t = build_full_ticket("6/49", {"hard_core": [1, 2, 3], "guarantee": 3})
    assert t["error"] and "variants" not in t
