"""Compare Romanian reduced-scheme suggestions at a user-selected budget."""

from __future__ import annotations

from fractions import Fraction

from covering.lr_any_prize_649 import recommend_649
from covering.lr_any_prize_joker import recommend_joker_any_prize
from covering.lr_portfolio import recommend_main_hits
from loto_enterprise.core.lotteries import games_for_country

MIN_BUDGET_LEI = 100
DEFAULT_BUDGET_LEI = 200
FOUR_PLUS = "four_plus"
ANY_PRIZE = "any_prize"


def recommend_games(budget_lei: int, objective: str, game_id: str | None = None) -> list[dict]:
    """Rank playable Romanian LR plans by their exact per-draw probability."""
    from covering import lr_schemes
    from covering.lr_catalog_cache import refresh_catalog
    refresh_catalog(lr_schemes.LR_DIR)
    if type(budget_lei) is not int or budget_lei < MIN_BUDGET_LEI:
        raise ValueError(f"Bugetul trebuie să fie de cel puțin {MIN_BUDGET_LEI} lei întregi.")
    if objective not in (FOUR_PLUS, ANY_PRIZE):
        raise ValueError(f"Obiectiv necunoscut: {objective!r}")

    games = games_for_country("RO")
    if game_id is not None:
        games = tuple(lot for lot in games if lot.game_id == game_id)
        if not games:
            raise ValueError(f"Joc necunoscut din catalogul românesc: {game_id!r}")

    budget_bani = budget_lei * 100
    results = []
    for lot in games:
        if objective == ANY_PRIZE and lot.game_id == "6/49":
            plan = recommend_649(budget_bani)
        elif objective == ANY_PRIZE and lot.game_id == "joker":
            plan = recommend_joker_any_prize(budget_bani)
        else:
            plan = recommend_main_hits(lot.game_id, budget_bani, threshold=4)
        if plan is None:
            continue

        codes = plan["codes"]
        joker_numbers = plan.get("joker_numbers")
        if joker_numbers is None:
            joker_numbers = list(range(1, len(codes) + 1)) if lot.game_id == "joker" else [None] * len(codes)
        zones = [
            {"code": code, "pool_size": len(numbers), "numbers": list(numbers), "joker": joker}
            for code, numbers, joker in zip(codes, plan["example_pools"], joker_numbers, strict=True)
        ]
        results.append({
            "game": lot.game_id,
            "game_name": lot.name,
            "objective": objective,
            "budget_bani": budget_bani,
            "price_bani": plan["price_bani"],
            "variants": plan["variants"],
            "attained": plan["attained"],
            "denominator": plan["denominator"],
            "upper": plan.get("upper"),
            "optimal": plan["optimal"],
            "anchor_size": plan["anchor_size"],
            "tickets": [
                zones[start:start + lot.per_ticket]
                for start in range(0, len(zones), lot.per_ticket)
            ],
        })
    return sorted(
        results,
        key=lambda item: (
            -Fraction(item["attained"], item["denominator"]),
            item["price_bani"], item["game"],
        ),
    )
