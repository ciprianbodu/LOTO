"""Validate the experimental positional witnesses against the actual paid incumbent."""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from hashlib import sha256
from math import comb
from time import monotonic

from covering import lr_expanded_store, lr_portfolio
from covering.lr_budget import ANY_PRIZE
from covering.lr_catalog_cache import catalog_cache, certificate_snapshot
from covering.lr_schemes import lr_schemes
from loto_enterprise.core.lotteries import require_lottery


_logger = logging.getLogger(__name__)
WITNESS_EVALUATOR = "independent-intersections-v1"


def geometry(lot):
    return (
        round(lot.price * 100), lr_portfolio.FEE_BANI, lot.per_ticket,
        lot.draw_n, lot.pick_n, lot.max_n, lot.geo.second.max_n if lot.geo.second else 0,
    )


@catalog_cache(maxsize=9)
def _catalog_hash(game):
    schemes = sorted(lr_schemes(game), key=lambda scheme: scheme.code)
    canonical = [[s.code, s.n, s.variants] for s in schemes]
    return sha256(json.dumps(canonical, separators=(",", ":")).encode("ascii")).hexdigest()


@lru_cache(maxsize=2)
def _index(blob, method, evaluator):
    if not blob:
        return {}, {}, {}
    data = json.loads(blob)
    if (data["schema_version"] != 1 or data["method"] != method
            or data["evaluator"] != evaluator):
        return {}, {}, {}
    source = {k: data[k] for k in ("method", "evaluator", "archive_sha256", "candidate_sha256", "source_commit")}
    by_scope = {}
    for entry in data["certificates"]:
        key = tuple(entry[k] for k in ("game", "pool_size", "objective", "budget_bani", "joker_slots", "incumbent_cost_bani"))
        by_scope.setdefault(key, []).append(entry)
    return data["geometries"], source, by_scope


def _unpack(entry, lot, width, slots):
    schemes = {s.code: s for s in lr_schemes(lot.game_id)}
    groups = entry["tickets"]
    if not isinstance(groups, list) or not groups:
        raise ValueError("Șablonul LR nu conține bilete.")
    zones = []
    for i, ticket in enumerate(groups):
        if not 1 <= len(ticket) <= lot.per_ticket or (i < len(groups) - 1 and len(ticket) != lot.per_ticket):
            raise ValueError("Gruparea zonelor pe bilete fizice diferă de geometria verificată.")
        for zone in ticket:
            code, positions, slot = zone["code"], zone["positions"], zone["joker_slot"]
            scheme = schemes[code]
            if (type(code) is not int or len(positions) != scheme.n
                    or any(type(p) is not int or not 1 <= p <= width for p in positions)
                    or sorted(set(positions)) != positions):
                raise ValueError("Poziții nevalide în schema LR certificată.")
            valid_slot = type(slot) is int and 0 <= slot < slots if lot.geo.second else slot is None
            if not valid_slot:
                raise ValueError("Slot Joker incompatibil cu biletul plătit.")
            zones.append((code, tuple(p - 1 for p in positions), scheme.size, slot))
    return tuple(zones)


@catalog_cache(maxsize=192, semantic_key=lambda: _rules())
def _measured(game, width, objective, slots, zones, tariff):
    """Re-expand all official paid rows with their physical secondary assignments."""
    from covering.lr_pool_budget import _profile_counts

    lot = require_lottery(game, "RO")
    schemes = {s.code: s for s in lr_schemes(game)}
    bets = []
    for code, positions, count, slot in zones:
        scheme = schemes[code]
        valid_slot = type(slot) is int and 0 <= slot < slots if lot.geo.second else slot is None
        if (len(positions) != scheme.n or count != scheme.size
                or tuple(sorted(set(positions))) != positions or not all(0 <= p < width for p in positions)
                or not valid_slot):
            raise ValueError("Zona LR nu corespunde schemei sau sloturilor disponibile.")
        bets.extend((tuple(positions[p - 1] + 1 for p in row), slot) for row in scheme.variants)
    main_rows = tuple(dict.fromkeys(ticket for ticket, _ in bets))
    main = _profile_counts(main_rows, lot.draw_n, lot.max_n)
    threshold = 4 if game == "5/40" else 3
    prize = main[threshold - 1]
    denominator = comb(lot.max_n, lot.draw_n)
    multiplier = 1
    if lot.geo.second:
        multiplier = lot.geo.second.max_n
        prize *= multiplier
        for slot in set(played for _, played in bets):
            touch = {n for ticket, played in bets if played == slot for n in ticket}
            remaining = tuple(row for ticket in main_rows if (row := tuple(n for n in ticket if n not in touch)))
            outside = lot.max_n - len(touch)
            outside_wins = _profile_counts(remaining, lot.draw_n, outside)[threshold - 1]
            prize += denominator - comb(outside, lot.draw_n) - main[threshold - 1] + outside_wins
    return {
        "4+": main[3], "5+": main[4], "6+": main[5] if len(main) > 5 else 0,
        "denominator": denominator, "any_prize": prize, "prize_denominator": denominator * multiplier,
        "attained": prize if objective == ANY_PRIZE else main[3],
        "cost_bani": lr_portfolio.cost_bani(len(bets), len(zones), tariff[0], tariff[2]),
        "zones": len(zones), "physical_tickets": (len(zones) + lot.per_ticket - 1) // lot.per_ticket,
        "duplicate_paid_variants": len(bets) - len(set(bets)),
    }


def _rules():
    from loto_enterprise.benchmark.lr_provenance import layout_formulas
    return layout_formulas()


def _from_certificate(game, width, objective, budget, price, slots, zones, score, floor, duplicates, upper,
                      tariff, deadline, cancelled):
    from covering import lr_schemes as catalog
    from covering.lr_expanded_search import ALGORITHM_VERSION

    digest, blob = certificate_snapshot(catalog.LR_DIR)
    geometries, source, entries = _index(blob, ALGORITHM_VERSION, WITNESS_EVALUATOR)
    scope = (game, width, objective, budget, slots, price)
    candidates = entries.get(scope)
    if not candidates:
        return None
    fields = ("unit_bani", "ticket_fee_bani", "per_ticket", "draw_n", "pick_n", "max_n", "bonus_total")
    frozen = geometries[game]
    if tuple(frozen[k] for k in fields) != tariff or frozen["catalog_sha256"] != _catalog_hash(game):
        return None
    identity = lr_expanded_store.incumbent_hash(zones, tariff[2])
    current_key = (score, -price, -duplicates, tuple(minimum for _, minimum in floor))
    best = None
    for entry in candidates:
        if monotonic() >= deadline or (cancelled is not None and cancelled()):
            break
        selected = _unpack(entry, require_lottery(game, "RO"), width, slots)
        metrics = _measured(game, width, objective, slots, selected, tariff)
        expected = entry["metrics"]
        if (metrics != expected or any(type(v) is not int for v in expected.values())
                or len(entry["incumbent_sha256"]) != 64
                or any(c not in "0123456789abcdef" for c in entry["incumbent_sha256"])):
            raise ValueError("Evaluarea exactă a variantelor diferă de certificatul LR.")
        measure = metrics["attained"]
        tails = tuple(metrics[f"{t}+"] for t, _ in floor)
        if (measure > upper or measure < score or metrics["cost_bani"] > price
                or any(value < minimum for value, (_, minimum) in zip(tails, floor))):
            continue
        key = (measure, -metrics["cost_bani"], -metrics["duplicate_paid_variants"], tails)
        if key < current_key or (best is not None and key <= best[0]):
            continue
        matches = entry["incumbent_sha256"] == identity
        if not matches:
            # The source proves a different starting portfolio. Re-certify this
            # candidate against the actual paid bets, including all Joker slots.
            actual = _measured(game, width, objective, slots, zones, tariff)
            if (actual["attained"] != score or actual["cost_bani"] != price
                    or actual["duplicate_paid_variants"] != duplicates
                    or any(actual[f"{t}+"] != minimum for t, minimum in floor)):
                raise ValueError("Portofoliul de pornire diferă de numărătorii plătiți.")
        improves = key > current_key
        report = {
            "candidate": source["method"], "certificate": "proven_bound" if measure == upper else "feasible",
            "protected_numerators": {str(t): v for (t, _), v in zip(floor, tails)} if improves else dict(floor),
            "duplicate_paid_variants": metrics["duplicate_paid_variants"] if improves else duplicates,
            "expanded_certificate": {**source, "source_case_id": entry["source_case_id"],
                                     "source_member": entry["source_member"],
                                     "source_incumbent_sha256": entry["incumbent_sha256"],
                                     "incumbent_sha256": identity,
                                     "verification": "source_incumbent" if matches else "recertified_incumbent",
                                     "catalog_sha256": frozen["catalog_sha256"],
                                     "certificate_set_sha256": digest, "applied": improves},
        }
        best = key, (selected if improves else zones, measure, metrics["cost_bani"], report)
    return best[1] if best else None


def _cacheable(result):
    if result is None:
        return False
    evidence = result[3].get("search", {})
    return not evidence.get("fallback") and evidence.get("stopped") != "cancelled"


def enhance(game, width, objective, budget, price, slots, threshold, upper, zones,
            score, floor, duplicates, tariff, seconds, cancelled, *, persist=False):
    from covering.lr_expanded_search import refine_expanded

    if score == upper or seconds <= 0 or (cancelled is not None and cancelled()):
        return None
    deadline = monotonic() + min(2.0, seconds)
    try:
        certified = _from_certificate(game, width, objective, budget, price, slots, zones,
                                      score, floor, duplicates, upper, tariff, deadline, cancelled)
        if certified is not None:
            return certified
        if persist:
            path, context = lr_expanded_store.address(
                game, width, objective, budget, price, slots, threshold, upper, zones, score, floor, duplicates, tariff)
            previous = lr_expanded_store.load(path, context)
            if previous is not None:
                return previous
        remaining = deadline - monotonic()
        if remaining <= 0 or (cancelled is not None and cancelled()):
            return None
        refined = refine_expanded(game, width, objective, price, slots, threshold, upper,
                               zones, score, time_limit=remaining, cancelled=cancelled)
        _, result, paid, evidence = refined
        if (not score <= result <= upper or paid > price
                or any(evidence["protected_numerators"][t] < minimum for t, minimum in floor)):
            raise ValueError("Candidatul reduce un prag protejat sau depășește plafonul de cost.")
        if persist and _cacheable(refined):
            return lr_expanded_store.publish(path, context, refined)
        return refined
    except Exception:
        _logger.exception("Rafinarea portofoliului LR s-a întrerupt. Se păstrează biletele verificate.")
        return None


@catalog_cache(maxsize=192, semantic_key=_rules, cache_if=_cacheable)
def enhance_cached(game, width, objective, budget, price, slots, threshold, upper,
                   zones, score, floor, duplicates, tariff):
    return enhance(game, width, objective, budget, price, slots, threshold, upper,
                   zones, score, floor, duplicates, tariff, 2.0, None, persist=True)
