"""The confirmed numeric interval applies to the main urn in both budget modes."""

import pandas as pd
import pytest

from covering.lr_budget import FOUR_PLUS
from loto_enterprise.core import lr_budget_forecast as budget


def history(game):
    width = 5 if game == "joker" else 6
    rows = [
        {"date": f"0{i}-11-2026", **{f"n{j + 1}": j + i for j in range(width)}}
        for i in range(1, 5)
    ]
    frame = pd.DataFrame(rows)
    if game == "joker":
        frame["joker"] = [2, 5, 7, 10]
    return frame


@pytest.mark.parametrize("dynamic", [True, False])
@pytest.mark.parametrize("number_range", [(10, 40), (12, 38), (15, 35)])
def test_normal_and_inverse_benchmark_selected_main_range(monkeypatch, dynamic, number_range):
    first, last = number_range
    observed = []

    def score(excluded):
        observed.append(tuple(excluded))
        return {n: float(n) for n in range(1, 50)}, {"method": "test"}

    monkeypatch.setattr(budget, "_dynamic_scores", lambda game, code, draws, bounds, excluded: score(excluded))
    monkeypatch.setattr(
        budget, "restricted_bench_scores",
        lambda draws, bounds, game, width, excluded, target: score(excluded),
    )
    original = [1, 2, 3, 4, 5, 6, 13, 18, 24, 29, 33, 37]
    options = dict(
        histories={"6/49": ("h.csv", history("6/49"))}, dynamic=dynamic, pool_codes={},
        generated={"6/49": ("h.csv", {"hard_core": original, "lr_scheme_code": 57, "dynamic_method": dynamic})},
    )
    outside = set(range(1, first)) | set(range(last + 1, 50))
    normal, = budget.recommend_playable_games(200, FOUR_PLUS, "6/49", number_range=list(number_range), **options)
    inverse, = budget.recommend_playable_games(200, FOUR_PLUS, "6/49", number_range=number_range, inverse=True, **options)
    assert observed == [tuple(sorted(outside)), tuple(sorted(outside | set(original)))]
    for plan, blocked in ((normal, outside), (inverse, outside | set(original))):
        forecast = plan["forecast"]
        assert forecast["number_range"] == number_range
        assert forecast["eligible_count"] == 49 - len(blocked)
        expected = sorted(set(range(1, 50)) - blocked)[-12:]
        assert forecast["pool"] == plan["tickets"][0][0]["numbers"] == expected
    assert not normal["forecast"]["generated"]
    assert inverse["forecast"]["excluded_pool"] == original


def test_main_range_preserves_generated_joker_from_separate_urn():
    options = dict(
        histories={"joker": ("source.csv", history("joker"))}, pool_codes={}, joker_size=2,
        generated={"joker": ("source.csv", {
            "hard_core": list(range(1, 11)), "joker_pool": [2, 7], "lr_scheme_code": 24,
            "dynamic_method": True,
        })},
    )
    normal, = budget.recommend_playable_games(100, FOUR_PLUS, "joker", **options)
    restricted, = budget.recommend_playable_games(100, FOUR_PLUS, "joker", number_range=(15, 35), **options)
    for plan in (normal, restricted):
        assert plan["forecast"]["jokers"] == (2, 7)
        assert plan["tickets"][0][0]["joker"] == 2
    assert restricted["forecast"]["eligible_count"] == 21
    assert set(restricted["forecast"]["pool"]) <= set(range(15, 36))


def test_each_distinct_range_is_part_of_dynamic_forecast_cache():
    options = dict(histories={"6/49": ("source.csv", history("6/49"))}, pool_codes={})
    budget._dynamic_scores.cache_clear()
    for limits in [(10, 40), (12, 38), (15, 35), (12, 38)]:
        plan, = budget.recommend_playable_games(200, FOUR_PLUS, "6/49", number_range=limits, **options)
        assert plan["forecast"]["number_range"] == limits
    cache = budget._dynamic_scores.cache_info()
    assert cache.misses == 3 and cache.hits == 1


def test_normal_range_too_small_keeps_exact_budget_code_and_reports_capacity():
    plan, = budget.recommend_playable_games(
        200, FOUR_PLUS, "6/49", {"6/49": ("h.csv", history("6/49"))},
        pool_codes={}, number_range=(12, 21),
    )
    assert not plan["tickets"]
    assert "10 numere eligibile" in plan["forecast_error"]
    assert "Cod 57 necesită 12" in plan["forecast_error"]
