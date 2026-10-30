"""Resolve the category of an actual simple variant, never of a pool union.

Rules: https://www.loto.ro/?page_id=1070 (official FAQ, including the
2010-05-02 introduction of the fourth 6/49 category), Joker description at
https://www.loto.ro/?p=3904 and the official 5/40 brochure:
https://www.loto.ro/images/upload/pages/Brosura_Loto_5_din_40.pdf .

The 2026 Loto regulation, chapter VII article 6, awards a single prize to a
simple variant. Consequently a 5/40 category-I variant is not also category II.
Amounts, reports, draw identity and persistence belong to the official-prize
cache. This module neither estimates a payout nor adds together variant prizes.
"""

from __future__ import annotations

import math
import operator
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True, slots=True)
class PrizeMatch:
    category: str | None
    status: str
    main_hits: int | None = None
    joker_hit: bool | None = None
    possible_categories: tuple[str, ...] = ()
    reason: str = ""


_ROMAN = ("I", "II", "III", "IV", "V", "VI", "VII", "VIII")
_ALIASES = {
    "6/49": "loto_6_49", "loto 6/49": "loto_6_49", "loto_6_49": "loto_6_49",
    "5/40": "loto_5_40", "loto 5/40": "loto_5_40", "loto_5_40": "loto_5_40",
    "joker": "joker", "joker_urna1": "joker",
}
_GEOMETRY = {"loto_6_49": (6, 6, 49), "loto_5_40": (5, 6, 40), "joker": (5, 5, 45)}
_LABELS = {
    "loto_6_49": {"I": {"6/6"}, "II": {"5/6"}, "III": {"4/6"}, "IV": {"3/6"}},
    "loto_5_40": {"I": {"5/6*", "5/5"}, "II": {"5/6"}, "III": {"4/6"}},
    "joker": {
        "I": {"5/5+J"}, "II": {"5/5"}, "III": {"4/5+J"}, "IV": {"4/5"},
        "V": {"3/5+J"}, "VI": {"3/5"}, "VII": {"2/5+J"}, "VIII": {"1/5+J"},
    },
}


def _number(value) -> int:
    if isinstance(value, bool):
        raise ValueError("Boolean is not a lottery number")
    try:
        return operator.index(value)
    except TypeError:
        if isinstance(value, str) and re.fullmatch(r"[0-9]+", value.strip()):
            return int(value)
        if isinstance(value, float) and math.isfinite(value) and value.is_integer():
            return int(value)
        raise ValueError("Lottery number must be an integer") from None


def _numbers(values, count, maximum):
    numbers = tuple(_number(value) for value in values)
    if len(numbers) != count or len(set(numbers)) != count:
        raise ValueError(f"Expected {count} distinct lottery numbers")
    if any(not 1 <= number <= maximum for number in numbers):
        raise ValueError("Lottery number is outside the game's range")
    return numbers


def _draw_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(str(value).strip(), fmt).date()
        except (ValueError, TypeError):
            pass
    return None


def _field(value, name, fallback=None):
    return value.get(name, fallback) if isinstance(value, Mapping) else getattr(value, name, fallback)


def _published_categories(values):
    if values is None:
        return None
    if isinstance(values, str):
        values = [values]
    if isinstance(values, Mapping):
        values = [
            value if _field(value, "category") is not None else {"category": key, **(value if isinstance(value, Mapping) else {})}
            for key, value in values.items()
        ]
    published = {}
    for value in values:
        raw = str(_field(value, "category", value)).strip().upper()
        found = re.fullmatch(r"(?:CAT(?:EGORIA)?\.?\s*)?([IVX]+)(?:\s*\(([^()]*)\))?", raw)
        if not found or found[1] not in _ROMAN:
            continue
        label = _field(value, "matches_label", found[2] or "")
        label = re.sub(r"\s+", "", str(label or "")).upper().replace("JOKER", "J")
        published.setdefault(found[1], set()).add(label)
    return published


def resolve_variant_category(
    game,
    variant,
    official_numbers,
    *,
    official_joker=None,
    joker_hit: bool | None = None,
    draw_date=None,
    available_categories=None,
    draw_order_known: bool = True,
) -> PrizeMatch:
    """Return the single category of one actual variant against an exact draw.

    ``variant`` must contain the simple variant's six numbers (6/49), five
    numbers (5/40), or Joker's five main numbers followed by its urn-2 pick.
    A five-number Joker variant can instead supply its own known ``joker_hit``.
    A pool, a hit count, or a union of several variants is not an input.

    ``official_numbers`` preserves official extraction order. Set
    ``draw_order_known=False`` if that order is unavailable: five hits at 5/40
    then remain ambiguous between I and II. A supplied category table is
    checked for the actual category and its published match label; a missing
    category never causes a downgrade or a guessed payout.
    """
    canonical = _ALIASES.get(str(game).strip().casefold())
    if canonical is None:
        return PrizeMatch(None, "unsupported_game")
    pick, drawn, maximum = _GEOMETRY[canonical]
    if isinstance(variant, (str, bytes)):
        return PrizeMatch(None, "invalid_variant")
    if isinstance(official_numbers, (str, bytes)):
        return PrizeMatch(None, "invalid_draw")
    try:
        raw_variant = tuple(variant) if variant is not None else ()
    except TypeError:
        return PrizeMatch(None, "invalid_variant")
    try:
        raw_draw = tuple(official_numbers) if official_numbers is not None else ()
    except TypeError:
        return PrizeMatch(None, "invalid_draw")
    if not raw_variant:
        return PrizeMatch(None, "missing_variant")
    if not raw_draw:
        return PrizeMatch(None, "missing_draw")
    chosen_joker = None
    try:
        if canonical == "joker" and len(raw_variant) == 6:
            chosen_joker = _numbers(raw_variant[-1:], 1, 20)[0]
            raw_variant = raw_variant[:5]
        selected = _numbers(raw_variant, pick, maximum)
    except (ValueError, TypeError):
        return PrizeMatch(None, "invalid_variant")
    try:
        if canonical == "joker" and len(raw_draw) == 6:
            embedded_joker = _numbers(raw_draw[-1:], 1, 20)[0]
            if official_joker is not None and _number(official_joker) != embedded_joker:
                return PrizeMatch(None, "invalid_draw", reason="Conflicting official Joker numbers")
            official_joker, raw_draw = embedded_joker, raw_draw[:5]
        actual = _numbers(raw_draw, drawn, maximum)
        if canonical == "joker" and official_joker is not None:
            official_joker = _numbers([official_joker], 1, 20)[0]
    except (ValueError, TypeError):
        return PrizeMatch(None, "invalid_draw")
    hits = len(set(selected).intersection(actual))
    published = _published_categories(available_categories)
    matched_joker = None
    category = None
    if canonical == "loto_6_49":
        if hits == 3:
            when = _draw_date(draw_date)
            if when is not None and when < date(2010, 5, 2):
                return PrizeMatch(None, "no_prize", hits)
            if when is None and (published is None or "IV" not in published):
                return PrizeMatch(None, "missing_draw_date", hits, possible_categories=("IV",))
        category = {6: "I", 5: "II", 4: "III", 3: "IV"}.get(hits)
    elif canonical == "loto_5_40":
        if hits == 5 and not draw_order_known:
            return PrizeMatch(None, "ambiguous_category", hits, possible_categories=("I", "II"), reason="Official first-five extraction order is required")
        category = "I" if hits == 5 and set(selected) == set(actual[:5]) else {5: "II", 4: "III"}.get(hits)
    else:
        if joker_hit is not None and not isinstance(joker_hit, bool):
            return PrizeMatch(None, "invalid_variant", hits, reason="joker_hit must be a boolean or None")
        if chosen_joker is not None and official_joker is not None:
            matched_joker = chosen_joker == official_joker
            if joker_hit is not None and joker_hit != matched_joker:
                return PrizeMatch(None, "invalid_variant", hits, reason="Joker hit flag contradicts the actual variant")
        else:
            matched_joker = joker_hit
        if hits and matched_joker is None:
            possible = {5: ("I", "II"), 4: ("III", "IV"), 3: ("V", "VI"), 2: ("VII",), 1: ("VIII",)}[hits]
            return PrizeMatch(None, "missing_joker", hits, possible_categories=possible)
        category = (
            {5: "I", 4: "III", 3: "V", 2: "VII", 1: "VIII"}.get(hits)
            if matched_joker else {5: "II", 4: "IV", 3: "VI"}.get(hits)
        )
    if category is None:
        return PrizeMatch(None, "no_prize", hits, matched_joker)
    if published is not None:
        if category not in published:
            return PrizeMatch(None, "category_unavailable", hits, matched_joker, (category,))
        if any(label and label not in _LABELS[canonical][category] for label in published[category]):
            return PrizeMatch(None, "unsupported_rules", hits, matched_joker, (category,), "Published category match label differs from the supported rules")
    return PrizeMatch(category, "matched", hits, matched_joker, (category,))
