"""Historical estimates for the actual wheel variants saved by walk-forward.

Published unit prizes are gross historical values. Playing additional variants
could change the prize fund and the per-winner payouts; this is not a claim that
any ticket was bought or that the estimate was paid.
"""
from __future__ import annotations

import re
from datetime import date
from collections.abc import Mapping

from loto_enterprise.core.pool_prize import (
    PoolPrizeEstimate, _GAMES, _GEOMETRY, _LABELS, _date, _number, _numbers,
)
from loto_enterprise.core.prize_categories import resolve_variant_category


def estimate_generated_prize(game, variants, draw, *, target_numbers, target_date,
                             expected_count=None, variant_prize_groups=None,
                             virtual_full_system=False, joker_hit=None,
                             pool_numbers=None):
    """Evaluate only a complete, actual list of generated variants or its summary.

    A compact summary must have version=1, total_variants, and groups of a real
    representative variant and its multiplicity. Each group corresponds to the
    same set of matched main numbers and the same selected Joker ball. The total
    includes losing variants so completeness is testable. The official archive
    determines the category and supplies unit payouts. A virtual wheel or an
    incomplete old compact cache is explicitly unavailable.
    """
    canonical = _GAMES.get(str(game).strip().casefold())
    if canonical is None:
        return PoolPrizeEstimate("unsupported_game", reason="Joc fără arhivă oficială compatibilă")
    if virtual_full_system:
        return PoolPrizeEstimate("variants_unavailable", reason="Istoricul virtual nu conține variante generate")
    try:
        pick, draw_n, maximum = _GEOMETRY[canonical]
        actual = _numbers(getattr(draw, "numbers"), draw_n, maximum)
        requested = _numbers(target_numbers, draw_n, maximum)
        when = _date(getattr(draw, "draw_date"))
        if (_GAMES.get(str(getattr(draw, "game", "")).strip().casefold()) != canonical
                or not when or when != _date(target_date) or set(actual) != set(requested)):
            return PoolPrizeEstimate("draw_mismatch", reason="Extragerea oficială nu corespunde istoricului")
        if joker_hit is not None and not isinstance(joker_hit, bool):
            raise ValueError("indicator Joker invalid")
        if expected_count is not None:
            expected_count = _number(expected_count)
            if expected_count <= 0:
                raise ValueError("număr de variante invalid")
        pool = None if pool_numbers is None else set(_numbers(pool_numbers, None, maximum))
        if pool is not None and len(pool) < pick:
            raise ValueError("pool incomplet")
        if variant_prize_groups is not None:
            if (not isinstance(variant_prize_groups, Mapping) or type(variant_prize_groups.get("version")) is not int
                    or variant_prize_groups.get("version") != 1):
                raise ValueError("rezumat necunoscut")
            groups = variant_prize_groups.get("groups")
            if not isinstance(groups, (list, tuple)) or not groups:
                raise ValueError("rezumat fără variante")
            total = _number(variant_prize_groups.get("total_variants"))
            if total <= 0 or (expected_count is not None and total != expected_count):
                raise ValueError("număr de variante necorespunzător")
            grouped = []
            for group in groups:
                if not isinstance(group, Mapping):
                    raise ValueError("grup invalid")
                n = _number(group.get("count"))
                if n <= 0:
                    raise ValueError("multiplicitate invalidă")
                grouped.append((group.get("variant"), n))
            if sum(n for _, n in grouped) != total:
                raise ValueError("rezumat incomplet")
        else:
            if isinstance(variants, (str, bytes)) or variants is None:
                return PoolPrizeEstimate("variants_unavailable", reason="Variantele generate nu sunt salvate")
            try:
                variants = tuple(variants)
            except TypeError:
                return PoolPrizeEstimate("variants_unavailable", reason="Variantele generate nu sunt salvate")
            if not variants or (expected_count is not None and len(variants) != expected_count):
                return PoolPrizeEstimate("variants_unavailable", reason="Istoricul nu păstrează toate variantele generate")
            grouped = [(variant, 1) for variant in variants]
        counts = {}
        signatures = set()
        for variant, n in grouped:
            if isinstance(variant, (str, bytes)) or variant is None:
                raise ValueError("variantă lipsă")
            values = tuple(variant)
            if canonical == "joker" and len(values) != pick + 1:
                return PoolPrizeEstimate("missing_joker", counts, reason="Varianta Joker nu conține bila aleasă din Urna 2")
            if canonical == "joker" and getattr(draw, "joker", None) is None:
                return PoolPrizeEstimate("missing_joker", counts, reason="Bila Joker extrasă nu este confirmată în arhivă")
            main = values[:pick] if canonical == "joker" else values
            main = _numbers(main, pick, maximum)
            if pool is not None and not set(main).issubset(pool):
                raise ValueError("varianta nu aparține pool-ului")
            chosen_joker = _number(values[-1]) if canonical == "joker" and len(values) == pick + 1 else None
            if chosen_joker is not None and not 1 <= chosen_joker <= 20:
                raise ValueError("număr Joker invalid")
            signature = (tuple(sorted(set(main) & set(actual))), chosen_joker)
            if variant_prize_groups is not None:
                if signature in signatures:
                    raise ValueError("grupe de variante repetate")
                signatures.add(signature)
            match = resolve_variant_category(
                canonical, values, actual,
                official_joker=getattr(draw, "joker", None), joker_hit=joker_hit,
                draw_date=when, draw_order_known=getattr(draw, "order_known", False),
            )
            if match.status == "no_prize":
                continue
            if match.status != "matched":
                reason = {"ambiguous_category": "Ordinea oficială a primelor cinci numere este indisponibilă",
                          "missing_joker": "Numărul Joker al variantei nu este confirmat"}.get(match.status, "Varianta sau extragerea nu pot fi validate")
                return PoolPrizeEstimate(match.status, counts, reason=reason)
            counts[match.category] = counts.get(match.category, 0) + n
    except (AttributeError, TypeError, ValueError, OverflowError) as exc:
        return PoolPrizeEstimate("invalid_context", reason=str(exc))

    if not counts:
        return PoolPrizeEstimate("ready", total_bani=0)
    historical = when < date(2005, 7, 1) and str(getattr(draw, "currency", "lei") or "lei").strip().casefold() == "lei"
    try:
        published = {}
        for entry in getattr(draw, "categories"):
            category = str(entry.category).strip().upper()
            if category in published:
                return PoolPrizeEstimate("unsupported_rules", counts, reason="Categorie duplicată în arhivă")
            published[category] = entry
        missing = []
        amount = 0
        for category, count in counts.items():
            entry = published.get(category)
            if entry is None:
                missing.append(category)
                continue
            label = re.sub(r"\s+", "", str(getattr(entry, "matches_label", "") or "")).upper().replace("JOKER", "J")
            if label and label not in _LABELS[canonical][category]:
                return PoolPrizeEstimate("unsupported_rules", counts, reason=f"Eticheta categoriei {category} nu corespunde regulilor")
            winners, unit = getattr(entry, "winners", None), getattr(entry, "amount_bani", None)
            if type(winners) is not int or winners <= 0 or type(unit) is not int or unit < 0:
                missing.append(category)
            else:
                amount += count * unit
        if missing:
            return PoolPrizeEstimate("missing_prizes", counts, missing_categories=tuple(missing), reason="Valori lipsă sau categorii în report")
        if historical:
            return PoolPrizeEstimate("historical_currency", counts, amount, reason="Valoare istorică publicată, moneda neconfirmată; fără conversie")
        return PoolPrizeEstimate("ready", counts, amount)
    except (TypeError, AttributeError, ValueError) as exc:
        return PoolPrizeEstimate("invalid_prizes", counts, reason=str(exc))
