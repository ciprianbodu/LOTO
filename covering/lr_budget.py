"""Compare Romanian reduced-scheme suggestions at a user-selected budget."""

from __future__ import annotations

from fractions import Fraction
from math import comb, isfinite

from covering.lr_any_prize_649 import recommend_649
from covering.lr_any_prize_joker import recommend_joker_any_prize
from covering.lr_portfolio import cost_bani, recommend_main_hits
from loto_enterprise.core.lotteries import games_for_country

MIN_BUDGET_LEI = 100
DEFAULT_BUDGET_LEI = 200
FOUR_PLUS = "four_plus"
ANY_PRIZE = "any_prize"
SINGLE_SCHEME_MODE = "nearest_single_scheme"


def single_scheme_mode(mode):
    if mode is None:
        return False
    if mode != SINGLE_SCHEME_MODE:
        raise ValueError(f"Mod de buget LR necunoscut: {mode!r}.")
    return True


def nearest_single_scheme(lot, target_bani):
    """Choose by distance to the full price, then lower cost and official code."""
    from covering.lr_schemes import complete_lr_schemes

    unit = round(lot.price * 100)
    priced = ((scheme, cost_bani(scheme.size, 1, unit, lot.per_ticket))
              for scheme in complete_lr_schemes(lot.game_id))
    return min(priced, key=lambda pair: (abs(pair[1] - target_bani), pair[1], pair[0].code))


def _single_scheme_plan(lot, target_bani, objective):
    from covering.lr_schemes import _joker_prize_counts, scheme_numerators

    scheme, price = nearest_single_scheme(lot, target_bani)
    if objective == ANY_PRIZE and lot.geo.second:
        denominator, attained, _ = _joker_prize_counts(scheme)
    else:
        threshold = 3 if objective == ANY_PRIZE and lot.game_id == "6/49" else 4
        attained = scheme_numerators(scheme)[threshold]
        denominator = comb(lot.max_n, lot.draw_n)
    return {
        "codes": [scheme.code], "example_pools": [list(range(1, scheme.n + 1))],
        "price_bani": price, "variants": scheme.size, "attained": attained,
        "denominator": denominator, "upper": None, "optimal": False, "anchor_size": 0,
    }


def maximum_scheme_budget_bani() -> int:
    """Price of the most expensive single official zone, including its physical ticket."""
    from covering import lr_schemes
    from covering.lr_catalog_cache import refresh_catalog

    refresh_catalog(lr_schemes.LR_DIR)
    return max((
        cost_bani(scheme.size, 1, round(lot.price * 100), lot.per_ticket)
        for lot in games_for_country("RO")
        for scheme in lr_schemes.lr_schemes(lot.game_id)
    ), default=MIN_BUDGET_LEI * 100)


def budget_in_bani(lei: int | float) -> int:
    if type(lei) is int:
        return lei * 100
    if type(lei) is float and isfinite(lei):
        bani = Fraction(str(lei)) * 100
        if bani.denominator == 1:
            return bani.numerator
    raise ValueError("Bugetul trebuie exprimat în lei, cu cel mult două zecimale.")


def budget_in_lei(bani: int) -> int | float:
    return bani // 100 if bani % 100 == 0 else bani / 100


def recommend_games(
    budget_lei: int | float, objective: str, game_id: str | None = None, *, single_scheme=False,
) -> list[dict]:
    """Rank playable Romanian LR plans by their exact per-draw probability."""
    from covering import lr_schemes
    from covering.lr_catalog_cache import refresh_catalog
    refresh_catalog(lr_schemes.LR_DIR)
    budget_bani = budget_in_bani(budget_lei)
    if budget_bani < MIN_BUDGET_LEI * 100:
        raise ValueError(f"Bugetul trebuie să fie de cel puțin {MIN_BUDGET_LEI} lei.")
    if objective not in (FOUR_PLUS, ANY_PRIZE):
        raise ValueError(f"Obiectiv necunoscut: {objective!r}")

    games = games_for_country("RO")
    if game_id is not None:
        games = tuple(lot for lot in games if lot.game_id == game_id)
        if not games:
            raise ValueError(f"Joc necunoscut din catalogul românesc: {game_id!r}")

    results = []
    for lot in games:
        if single_scheme:
            plan = _single_scheme_plan(lot, budget_bani, objective)
        elif objective == ANY_PRIZE and lot.game_id == "6/49":
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
            **({"budget_mode": SINGLE_SCHEME_MODE} if single_scheme else {}),
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
