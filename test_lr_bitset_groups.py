"""Exact LR prize-group parity for the large-scheme positional bitsets."""

from itertools import combinations
from random import Random

import pytest

from covering import lr_schemes as lr


SCHEMES = lr.lr_schemes()


def _oracle(scheme, pool, drawn, joker=None):
    """Group actual mapped tickets, without positional masks or bitsets."""
    numbers = sorted({int(n) for n in pool})
    drawn = {int(n) for n in drawn}
    groups = {}
    for row in scheme.variants:
        ticket = [numbers[p - 1] for p in row]
        signature = frozenset(set(ticket) & drawn)
        if signature in groups:
            groups[signature]["count"] += 1
        else:
            if scheme.game == "joker":
                ticket.append(joker)
            groups[signature] = {"variant": ticket, "count": 1}
    return {
        "version": 1,
        "total_variants": scheme.size,
        "groups": list(groups.values()),
    }


@pytest.mark.parametrize("scheme", SCHEMES, ids=lambda s: f"{s.game}-cod{s.code}")
@pytest.mark.parametrize("hits", [0, 1, 2, 3, 4, 5, 6, 7, "all"])
def test_all_official_schemes_match_actual_tickets(scheme, hits):
    count = scheme.n if hits == "all" else hits
    if count > scheme.n:
        pytest.skip("Intersection larger than this pool")
    rng = Random(20261105 + scheme.code + count)
    maximum = lr._GAMES[scheme.game][3]
    for repeat in range(3):
        pool = rng.sample(range(1, maximum + 1), scheme.n)
        drawn = rng.sample(pool, count) + [maximum + 10]
        # Pool duplicates and drawn ordering keep the existing normalization.
        given_pool = pool + pool[:2] if repeat == 1 else pool
        rng.shuffle(drawn)
        expected = _oracle(scheme, given_pool, drawn, joker=7)
        actual = lr.scheme_prize_groups(scheme, given_pool, drawn, joker=7)
        assert actual == expected
        assert sum(group["count"] for group in actual["groups"]) == scheme.size


def _large_copy():
    source = next(s for s in SCHEMES if s.game == "5/40" and s.code == 48)
    return lr.LRScheme(source.game, source.code, source.n, source.variants)


def test_large_scheme_memberships_are_cached_only_on_that_instance():
    scheme = _large_copy()
    before_hash = hash(scheme)
    pool = list(range(1, scheme.n + 1))
    lr.scheme_prize_groups(scheme, pool, [1, 3, 5, 7])
    memberships = scheme._position_memberships
    assert scheme._position_memberships is memberships
    assert hash(scheme) == before_hash
    changed = lr.LRScheme(scheme.game, scheme.code, scheme.n, scheme.variants[::-1])
    assert "_position_memberships" not in changed.__dict__
    assert lr.scheme_prize_groups(changed, pool, [1, 3, 5, 7]) == _oracle(
        changed, pool, [1, 3, 5, 7]
    )
    assert changed._position_memberships != memberships


def test_returned_groups_are_fresh_mutable_values():
    scheme = _large_copy()
    pool = list(range(1, scheme.n + 1))
    drawn = [1, 3, 5, 7, 9, 11]
    expected = _oracle(scheme, pool, drawn)
    first = lr.scheme_prize_groups(scheme, pool, drawn)
    first["groups"][0]["variant"][0] = -1
    first["groups"][0]["count"] = -1
    first["groups"].append({"variant": [], "count": 1000})
    assert lr.scheme_prize_groups(scheme, pool, drawn) == expected


def test_more_than_six_hits_keeps_the_bounded_linear_path():
    scheme = _large_copy()
    pool = list(range(1, scheme.n + 1))
    for drawn in [pool[:7], pool]:
        assert lr.scheme_prize_groups(scheme, pool, drawn) == _oracle(
            scheme, pool, drawn
        )
    assert "_position_memberships" not in scheme.__dict__


def test_small_schemes_do_not_build_memberships():
    original = next(s for s in SCHEMES if s.game == "6/49" and s.code == 59)
    scheme = lr.LRScheme(original.game, original.code, original.n, original.variants)
    pool = list(range(1, scheme.n + 1))
    for count in range(7):
        assert lr.scheme_prize_groups(scheme, pool, pool[:count]) == _oracle(
            scheme, pool, pool[:count]
        )
    assert "_position_memberships" not in scheme.__dict__


@pytest.mark.parametrize("joker", [None, 0, True, "invalid", 21, 7])
def test_nonstandard_rows_and_joker_values_keep_their_existing_semantics(joker):
    # Mixed widths, empty rows and repeated rows are possible in a manually
    # constructed LRScheme. The loader validates official files separately.
    rows = tuple(combinations(range(1, 11), 4)) + tuple(combinations(range(1, 11), 3))
    rows = ((),) + rows + rows[:5]
    scheme = lr.LRScheme("joker", 999, 10, rows)
    pool = [19, 2, 11, 4, 15, 6, 13, 8, 17, 10]
    for count in range(7):
        drawn = pool[:count]
        assert lr.scheme_prize_groups(scheme, pool, drawn, joker=joker) == _oracle(
            scheme, pool, drawn, joker=joker
        )


def test_empty_scheme_retains_empty_groups():
    scheme = lr.LRScheme("5/40", 999, 2, ())
    assert lr.scheme_prize_groups(scheme, [1, 2], [1]) == {
        "version": 1,
        "total_variants": 0,
        "groups": [],
    }


def test_bad_pool_size_is_still_rejected_before_building_memberships():
    scheme = _large_copy()
    with pytest.raises(ValueError, match="cere 19 numere"):
        lr.scheme_prize_groups(scheme, [1, 2], [1])
    assert "_position_memberships" not in scheme.__dict__
