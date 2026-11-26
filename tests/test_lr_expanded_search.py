"""Regressions for the frozen whole-zone refinement, independent of the UI."""

from math import inf

import pytest

from covering import lr_expanded_search as expanded
from covering.lr_pool_budget import _zone_catalog
from covering.lr_schemes import lr_schemes
from loto_enterprise.core.lotteries import require_lottery


def duplicated_zone(slots, **kwargs):
    zones = tuple((45, tuple(range(7)), 5, slot) for slot in slots)
    return zones, expanded.refine_expanded(
        "joker", 8, "four_plus", 7050, 3, 4, kwargs.pop("upper", 2000), zones, 895, **kwargs
    )


@pytest.mark.parametrize(("slots", "duplicates"), [((2, 2), 5), ((2, 1), 0)])
@pytest.mark.parametrize("stop", [{"time_limit": 0}, {"cancelled": lambda: True}])
def test_preserves_actual_paid_secondary_assignments_on_stop(slots, duplicates, stop):
    zones, (selected, score, price, audit) = duplicated_zone(slots, **stop)
    assert (selected, score, price) == (zones, 895, 7050)
    assert audit["protected_numerators"] == {"4": 895, "5": 5}
    assert audit["duplicate_paid_variants"] == duplicates


def test_proven_bound_short_circuits_before_any_search(monkeypatch):
    def unexpected(*args):
        pytest.fail("The full portfolio already attains its upper bound")

    monkeypatch.setattr(expanded._ExpandedSearch, "search", unexpected)
    zones, (selected, _, _, audit) = duplicated_zone((2, 1), upper=895)
    assert selected == zones
    assert audit["certificate"] == "proven_bound"


def test_search_error_preserves_completed_incumbent(monkeypatch):
    def interrupted(*args):
        raise ArithmeticError("simulated numerical error")

    monkeypatch.setattr(expanded._ExpandedSearch, "search", interrupted)
    zones, (selected, _, _, audit) = duplicated_zone((2, 1))
    assert selected == zones
    assert audit["search"]["fallback"] == "search_error"


def test_independent_recount_rejects_corrupt_winner(monkeypatch):
    from dataclasses import replace

    def corrupt(engine, current):
        engine.best = replace(current, score=current.score + 1)

    monkeypatch.setattr(expanded._ExpandedSearch, "search", corrupt)
    zones, (selected, score, price, audit) = duplicated_zone((2, 2))
    assert (selected, score, price) == (zones, 895, 7050)
    assert audit["search"]["fallback"] == "verification_error"


def test_retains_catalogue_and_adds_only_the_frozen_64_placements():
    lot = require_lottery("joker", "RO")
    scheme = next(s for s in lr_schemes("joker") if s.code == 45)
    engine = expanded._ExpandedSearch(lot, 12, "four_plus", 7050, 3, 4, None, 30, None)
    existing = _zone_catalog("joker", 12, 4, False, (45,))
    all_zones = engine.expanded_zones((scheme,), inf)[45]
    extra = all_zones[len(existing):]
    assert tuple(all_zones[:len(existing)]) == existing
    assert len(extra) == 64
    assert len(set(zone.positions for zone in all_zones)) == len(all_zones)
    assert extra[0].positions == (0, 1, 4, 5, 8, 10, 11)
    assert extra[-1].positions == (0, 1, 2, 4, 9, 10, 11)
