"""Last CSV draw vs production pool must not be confused with walk-forward 🔥."""

from app_nicegui import draw_vs_production_pool


def test_hits_and_misses_split():
    hits, miss = draw_vs_production_pool([1, 15, 5, 40, 23], [1, 5, 6, 15, 23, 37])
    assert hits == [1, 15, 5, 23]
    assert miss == [40]


def test_empty_pool_is_all_miss():
    hits, miss = draw_vs_production_pool([1, 2, 3], [])
    assert hits == []
    assert miss == [1, 2, 3]


def test_full_overlap():
    hits, miss = draw_vs_production_pool([10, 20], [20, 10, 30])
    assert hits == [10, 20]
    assert miss == []
