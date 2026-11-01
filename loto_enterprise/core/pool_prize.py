"""Pure full-system prize estimates using published historical unit values.

These are gross historical-value estimates, not actual or counterfactual payouts:
lottery distributions can change when the number of winning variants changes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from math import comb
import operator
import re


@dataclass(frozen=True)
class PoolPrizeEstimate:
    status: str
    category_counts: dict[str, int] = field(default_factory=dict)
    total_bani: int | None = None
    missing_categories: tuple[str, ...] = ()
    reason: str = ""


_GAMES = {
    "6/49": "loto_6_49", "loto 6/49": "loto_6_49", "loto_6_49": "loto_6_49",
    "5/40": "loto_5_40", "loto 5/40": "loto_5_40", "loto_5_40": "loto_5_40",
    "joker": "joker", "joker_urna1": "joker",
}
_GEOMETRY = {"loto_6_49": (6, 6, 49), "loto_5_40": (5, 6, 40), "joker": (5, 5, 45)}
_LABELS = {
    "loto_6_49": {"I": {"6/6"}, "II": {"5/6"}, "III": {"4/6"}, "IV": {"3/6"}},
    "loto_5_40": {"I": {"5/6*", "5/5"}, "II": {"5/6"}, "III": {"4/6"}},
    "joker": {"I": {"5/5+J"}, "II": {"5/5"}, "III": {"4/5+J"}, "IV": {"4/5"},
              "V": {"3/5+J"}, "VI": {"3/5"}, "VII": {"2/5+J"}, "VIII": {"1/5+J"}},
}


def _number(value):
    if isinstance(value, bool):
        raise ValueError("număr boolean")
    try:
        return operator.index(value)
    except TypeError:
        if isinstance(value, str) and re.fullmatch(r"[0-9]+", value.strip()):
            return int(value)
        if isinstance(value, float) and value.is_integer():
            return int(value)
        raise ValueError("număr neîntreg") from None


def _numbers(values, count, maximum):
    if isinstance(values, (str, bytes)) or values is None:
        raise ValueError("set de numere indisponibil")
    numbers = tuple(_number(value) for value in values)
    if (count is not None and len(numbers) != count) or len(set(numbers)) != len(numbers):
        raise ValueError("număr invalid de elemente sau dubluri")
    if any(x < 1 or x > maximum for x in numbers):
        raise ValueError("număr din afara urnei")
    return numbers


def _date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value or "").strip().split("T")[0].split(" ")[0]
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            pass
    return None


def _choose(n, r):
    return comb(n, r) if 0 <= r <= n else 0


def estimate_pool_prize(game, pool_numbers, draw, *, target_numbers, target_date,
                        selected_joker=None, joker_hit=None):
    """Count every combination of the full pool; never claim an actual win.

    The target identity must match the official record exactly. Positive-count
    categories without a published unit prize, including REPORT, make the total
    unavailable rather than zero. ``selected_joker`` is the historical choice in
    urn 2; an independently validated boolean hit flag may be used when the
    picked ball is unavailable. Amounts are integer bani, pre-tax and before cost.
    """
    canonical = _GAMES.get(str(game).strip().casefold())
    if canonical is None:
        return PoolPrizeEstimate("unsupported_game", reason="Joc fără arhivă oficială compatibilă")
    try:
        pick, drawn_n, maximum = _GEOMETRY[canonical]
        pool = _numbers(pool_numbers, None, maximum)
        actual = _numbers(getattr(draw, "numbers"), drawn_n, maximum)
        target = _numbers(target_numbers, drawn_n, maximum)
        when = _date(getattr(draw, "draw_date"))
        requested_when = _date(target_date)
        draw_game = _GAMES.get(str(getattr(draw, "game", "")).strip().casefold())
        if len(pool) < pick or len(pool) > maximum:
            raise ValueError("dimensiune pool invalidă")
        if draw_game != canonical or not when or when != requested_when or set(actual) != set(target):
            return PoolPrizeEstimate("draw_mismatch", reason="Extragerea oficială nu corespunde istoricului")
        m = len(set(pool) & set(actual))
        v = len(pool)
        exact = lambda hits: _choose(m, hits) * _choose(v-m, pick-hits)
        if canonical == "loto_6_49":
            counts = {"I": exact(6), "II": exact(5), "III": exact(4)}
            if when >= date(2010, 5, 2):
                counts["IV"] = exact(3)
        elif canonical == "loto_5_40":
            n5 = exact(5)
            if n5 and m < 6 and not getattr(draw, "order_known", True):
                return PoolPrizeEstimate("ambiguous_order", reason="Ordinea oficială a primelor cinci numere este indisponibilă")
            first = 1 if (m == 6 or (getattr(draw, "order_known", True) and set(actual[:5]).issubset(pool))) else 0
            counts = {"I": first, "II": n5-first, "III": exact(4)}
        else:
            if joker_hit is not None and not isinstance(joker_hit, bool):
                raise ValueError("indicator Joker invalid")
            chosen = _number(selected_joker) if selected_joker is not None else None
            official = _number(getattr(draw, "joker", None)) if getattr(draw, "joker", None) is not None else None
            if chosen is not None and not 1 <= chosen <= 20 or official is not None and not 1 <= official <= 20:
                raise ValueError("număr Joker invalid")
            calculated = chosen == official if chosen is not None and official is not None else None
            if calculated is not None and joker_hit is not None and calculated != joker_hit:
                raise ValueError("indicatorul Joker contrazice numerele")
            matched = calculated if calculated is not None else joker_hit
            if matched is None and m > 0:
                return PoolPrizeEstimate("missing_joker", reason="Numărul Joker al variantei nu este confirmat")
            counts = ({"I": exact(5), "III": exact(4), "V": exact(3), "VII": exact(2), "VIII": exact(1)}
                      if matched else {"II": exact(5), "IV": exact(4), "VI": exact(3)})
    except (AttributeError, TypeError, ValueError, OverflowError) as exc:
        return PoolPrizeEstimate("invalid_context", reason=str(exc))

    counts = {category: count for category, count in counts.items() if count}
    if not counts:
        return PoolPrizeEstimate("ready", total_bani=0)
    historical_currency = when < date(2005, 7, 1) and str(getattr(draw, "currency", "lei") or "lei").strip().casefold() == "lei"
    try:
        categories = tuple(getattr(draw, "categories"))
        by_category = {}
        for entry in categories:
            key = str(entry.category).strip().upper()
            if key in by_category:
                return PoolPrizeEstimate("unsupported_rules", counts, reason="Categorie duplicată în arhivă")
            by_category[key] = entry
        missing = []
        total = 0
        for category, count in counts.items():
            entry = by_category.get(category)
            if entry is None:
                missing.append(category)
                continue
            label = re.sub(r"\s+", "", str(getattr(entry, "matches_label", "") or "")).upper().replace("JOKER", "J")
            if label and label not in _LABELS[canonical][category]:
                return PoolPrizeEstimate("unsupported_rules", counts, reason=f"Eticheta categoriei {category} nu corespunde regulilor")
            winners = getattr(entry, "winners", None)
            amount = getattr(entry, "amount_bani", None)
            if type(winners) is not int or winners <= 0 or type(amount) is not int or amount < 0:
                missing.append(category)
            else:
                total += count * amount
        if missing:
            return PoolPrizeEstimate("missing_prizes", counts, missing_categories=tuple(missing), reason="Valori lipsă sau categorii în report")
        if historical_currency:
            return PoolPrizeEstimate("historical_currency", counts, total, reason="Valoare istorică publicată, moneda neconfirmată; fără conversie")
        return PoolPrizeEstimate("ready", counts, total)
    except (TypeError, AttributeError, ValueError) as exc:
        return PoolPrizeEstimate("invalid_prizes", counts, reason=str(exc))
