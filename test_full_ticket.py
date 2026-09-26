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


_RANK = {n: float(26 - n) for n in range(1, 26)}  # 1 = cel mai bine clasat


def _data(pool, scores=_RANK, **extra):
    d = {"hard_core": pool, "guarantee": 3, "audit": {"timesfm_predictions": scores}}
    d.update(extra)
    return d


def test_6_49_pool_of_six_gets_the_next_ranked_number():
    """Din 6 numere iese o singura varianta de 6; biletul cere 3 distincte."""
    t = build_full_ticket("6/49", _data([1, 2, 3, 4, 5, 6]))
    assert t["pool"] == [1, 2, 3, 4, 5, 6, 7]
    assert len({tuple(v) for v in t["variants"]}) == 3
    assert "am adăugat 7" in t["note"]


def test_extension_follows_the_canonical_tie_break():
    """La scor egal castiga numarul mai mare, ca la pool-ul afisat."""
    scores = {n: 10.0 for n in range(1, 7)} | {20: 1.0, 30: 1.0}
    t = build_full_ticket("6/49", _data([1, 2, 3, 4, 5, 6], scores))
    assert t["pool"] == [1, 2, 3, 4, 5, 6, 30]


def test_joker_pool_over_ten_keeps_the_best_ranked_ten():
    pool = list(range(1, 13))
    t = build_full_ticket("joker", _data(pool, hard_core_joker=[7]))
    assert t["pool"] == list(range(1, 11))
    assert {n for v in t["variants"] for n in v[:5]} == set(range(1, 11))
    assert "În afara biletului: 11, 12" in t["note"]


def test_pools_within_ticket_limits_are_untouched():
    for game, pool in (("6/49", list(range(1, 17))), ("5/40", list(range(1, 7))),
                       ("joker", list(range(1, 11)))):
        t = build_full_ticket(game, _data(pool, hard_core_joker=[7]))
        assert t["pool"] == pool and t["note"] is None


def test_without_ranking_the_pool_stays_and_the_reason_is_shown():
    t = build_full_ticket("6/49", {"hard_core": [1, 2, 3, 4, 5, 6], "guarantee": 3})
    assert t["pool"] == [1, 2, 3, 4, 5, 6] and len(t["variants"]) == 1
    assert "clasamentul metodei lipsește" in t["note"]
