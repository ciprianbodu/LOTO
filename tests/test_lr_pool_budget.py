"""A playable official Joker zone always has one valid second-urn number."""

import pytest

from covering.lr_budget import ANY_PRIZE, FOUR_PLUS, recommend_games
from covering.lr_pool_budget import restrict_to_pool


@pytest.mark.parametrize("objective", (FOUR_PLUS, ANY_PRIZE))
@pytest.mark.parametrize("jokers", ((), None, "12", [False, 4], [0], [21], [4.5], [4, 4]))
def test_joker_layout_rejects_missing_or_invalid_second_urn(objective, jokers):
    plan, = recommend_games(100, objective, "joker")
    with pytest.raises(ValueError, match="Urna 2 Joker"):
        restrict_to_pool(plan, objective, [1, 3, 4, 7, 10, 20, 21], jokers)


@pytest.mark.parametrize("objective", (FOUR_PLUS, ANY_PRIZE))
def test_joker_layout_keeps_real_second_urn_assignment(objective):
    plan, = recommend_games(100, objective, "joker")
    pool = [1, 3, 4, 7, 10, 20, 21]
    layout = restrict_to_pool(plan, objective, pool, [20, 5])
    assert layout["tickets"]
    assert {zone["joker"] for ticket in layout["tickets"] for zone in ticket} <= {20, 5}
    assert layout["attained"] <= layout["upper"]
