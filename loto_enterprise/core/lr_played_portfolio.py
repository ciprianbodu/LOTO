"""One definition of the paid Romanian LR portfolio for generation and replay."""

from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
import json

from covering.lr_budget import nearest_single_scheme, single_scheme_mode
from covering.lr_portfolio import FEE_BANI
from covering.lr_schemes import scheme_tickets
from covering.lr_selection import resolve_lr_scheme
from loto_enterprise.core.lotteries import require_lottery
from loto_enterprise.core.prize_categories import resolve_variant_category


LAYOUT_VERSION = 1
CONTRACT_VERSION = 2


def layout_identity(plan):
    fields = {key: plan.get(key) for key in ("game", "objective", "budget_bani", "price_bani")}
    if mode := plan.get("budget_mode"):
        fields["budget_mode"] = mode
    fields["tickets"] = [[(z["code"], list(z["numbers"]), z.get("joker")) for z in ticket]
                         for ticket in plan["tickets"]]
    return sha256(json.dumps([LAYOUT_VERSION, fields], sort_keys=True,
                             separators=(",", ":")).encode()).hexdigest()


def evaluation_contract(plan, *, history_hash, inverse, number_range, dynamic, lookback,
                        hit_target, policy=None):
    """Describe exactly the settings and physical tickets to which a score refers."""
    portfolio = expand_plan(plan)
    lot = require_lottery(portfolio.game, "RO")
    return {
        "version": CONTRACT_VERSION, "game": portfolio.game,
        "draw_n": lot.draw_n, "pick_n": lot.pick_n,
        "second_urn": lot.geo.second.max_n if lot.geo.second else None,
        "objective": plan["objective"], "pool_hit_target": hit_target, "budget_bani": plan["budget_bani"],
        **({"budget_mode": plan["budget_mode"]} if "budget_mode" in plan else {}),
        "price_bani": portfolio.price_bani, "paid_variants": portfolio.paid,
        "distinct_variants": len(portfolio.variants), "duplicates": portfolio.paid - len(portfolio.variants),
        "layout": layout_identity(plan), "tickets": plan["tickets"], "pool": sorted(portfolio.pool),
        "base": list(number_range) if number_range else [1, lot.max_n], "inverse": bool(inverse),
        "max_consecutive_run": 2, "dynamic": bool(dynamic), "lookback": lookback,
        "history_hash": history_hash, "policy": policy or "catalog",
    }


@dataclass(frozen=True, slots=True)
class PlayedPortfolio:
    game: str
    pool: frozenset
    # Ordered main variant, its attached Joker, and paid multiplicity.
    variants: tuple
    price_bani: int
    paid: int


def expand_plan(plan):
    lot = require_lottery(plan["game"], "RO")
    variants = Counter()
    pool = set()
    for ticket in plan["tickets"]:
        if not 1 <= len(ticket) <= lot.per_ticket:
            raise ValueError("Număr de zone incompatibil cu biletul oficial.")
        for zone in ticket:
            numbers = tuple(zone["numbers"])
            scheme = resolve_lr_scheme(lot.geometry, zone["code"], len(numbers))
            if tuple(sorted(set(numbers))) != numbers or not all(1 <= n <= lot.max_n for n in numbers):
                raise ValueError("Numerele zonei LR trebuie să fie eligibile și în ordine crescătoare.")
            bonus = zone.get("joker")
            if lot.geo.second:
                if type(bonus) is not int or not 1 <= bonus <= lot.geo.second.max_n:
                    raise ValueError("Lipsește bila Joker validă pentru zona jucată.")
            elif bonus is not None:
                raise ValueError("Jocul are o singură urnă.")
            pool.update(numbers)
            variants.update((tuple(sorted(row)), bonus) for row in scheme_tickets(scheme, numbers))
    if not variants:
        raise ValueError("Portofoliul nu conține variante jucabile.")
    paid = variants.total()
    price = paid * round(lot.price * 100) + len(plan["tickets"]) * FEE_BANI
    if single_scheme_mode(plan.get("budget_mode")):
        expected, expected_price = nearest_single_scheme(lot, plan["budget_bani"])
        tickets = plan["tickets"]
        if (len(tickets) != 1 or len(tickets[0]) != 1 or tickets[0][0]["code"] != expected.code
                or price != expected_price):
            raise ValueError("Biletul trebuie să conțină numai codul LR cel mai apropiat de suma orientativă.")
    elif price > plan.get("budget_bani", price):
        raise ValueError("Costul variantelor și al biletelor nu corespunde planului LR.")
    if plan.get("price_bani", price) != price:
        raise ValueError("Costul variantelor și al biletelor nu corespunde planului LR.")
    forecast = (plan.get("forecast") or {}).get("pool", plan.get("forecast_pool", pool))
    if not pool.issubset(forecast):
        raise ValueError("Zona jucată depășește pool-ul selectat.")
    return PlayedPortfolio(lot.game_id, frozenset(forecast),
                           tuple((main, joker, count) for (main, joker), count in variants.items()), price, paid)


def grade_draw(expanded, drawn, joker=None, *, draw_date=None):
    """Grade complete paid variants. Absent draw date uses the current category schedule."""
    drawn = tuple(drawn)
    if expanded.game == "joker" and len(drawn) == 6:
        if joker is not None and joker != drawn[-1]:
            raise ValueError("Bila Joker declarată diferă de cea din rezultat.")
        drawn, joker = drawn[:5], drawn[-1]
    if expanded.game == "joker" and joker is None:
        # All unplayed bonus values produce the same grades. Evaluate that
        # outcome once alongside every distinct bonus attached to a paid zone.
        bonuses = {bonus for _, bonus, _ in expanded.variants}
        bonus_limit = require_lottery(expanded.game, "RO").geo.second.max_n
        unused = next((b for b in range(1, bonus_limit + 1) if b not in bonuses), None)
        if unused is not None:
            bonuses.add(unused)
        possible = [grade_draw(expanded, drawn, bonus, draw_date=draw_date) for bonus in sorted(bonuses)]
        result = possible[0].copy()
        for field in ("winning_variants", "distinct_winning_variants", "any_prize", "prize_counts"):
            first = possible[0][field]
            result[field] = first if all(row[field] == first for row in possible) else None
        counts = [row["winning_variants"] for row in possible]
        result["winning_variants_min"], result["winning_variants_max"] = min(counts), max(counts)
        return result

    actual = set(drawn)
    first_five = set(drawn[:5])
    categories, resolved = Counter(), {}
    best = distinct_wins = 0
    for variant, bonus, multiplicity in expanded.variants:
        hits = len(actual.intersection(variant))
        key = hits, bonus == joker, hits == 5 and first_five == set(variant)
        if key not in resolved:
            chosen = variant if bonus is None else (*variant, bonus)
            match = resolve_variant_category(expanded.game, chosen, drawn, official_joker=joker,
                                             draw_date=draw_date, available_categories=(
                                                 ("I", "II", "III", "IV") if expanded.game == "6/49" and draw_date is None else None))
            if match.status not in ("matched", "no_prize"):
                raise ValueError(f"Extragerea nu poate fi evaluată: {match.status}")
            resolved[key] = match.category
        best = max(best, hits)
        if category := resolved[key]:
            categories[category] += multiplicity
            distinct_wins += 1
    return {"pool_hits": len(expanded.pool & actual), "max_hits": best,
            "hit4": best >= 4, "hit5": best >= 5, "hit6": best >= 6 if expanded.game == "6/49" else None,
            "any_prize": bool(categories), "winning_variants": categories.total(),
            "distinct_winning_variants": distinct_wins, "prize_counts": dict(categories),
            "paid_variants": expanded.paid, "distinct_variants": len(expanded.variants),
            "duplicates": expanded.paid - len(expanded.variants), "price_bani": expanded.price_bani}


def grade_plan(plan, drawn, joker=None, *, draw_date=None):
    return grade_draw(expand_plan(plan), drawn, joker, draw_date=draw_date)
