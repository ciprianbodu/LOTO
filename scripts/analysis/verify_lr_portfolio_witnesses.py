"""Verify LR position templates with the official individual variants, without search."""

from argparse import ArgumentParser
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import re

from covering.lr_budget import ANY_PRIZE, FOUR_PLUS
from covering.lr_portfolio import FEE_BANI
from covering.lr_schemes import scheme_tickets
from covering.lr_selection import resolve_lr_scheme
from covering.probability import MAX_EXACT_POOL
from loto_enterprise.core.lotteries import require_lottery
from scripts.analysis.compare_lr_portfolio_search import exact


METRICS = ("attained", "4+", "5+", "6+", "denominator", "any_prize", "prize_denominator",
           "cost_bani", "zones", "physical_tickets", "duplicate_paid_variants")


def _integer(value, label, first=0, last=None):
    if type(value) is not int or value < first or (last is not None and value > last):
        raise ValueError(f"{label} trebuie să fie întreg în intervalul admis.")
    return value


def _positions(values, width, label):
    if not isinstance(values, list) or not values:
        raise ValueError(f"{label} trebuie să conțină poziții.")
    positions = tuple(_integer(v, label, 1, width) for v in values)
    if tuple(sorted(set(positions))) != positions:
        raise ValueError(f"{label} trebuie să fie distincte și în ordine crescătoare.")
    return positions


def _slot(value, count):
    if count:
        return _integer(value, "indexul Joker", 0, count - 1)
    if value is not None:
        raise ValueError("Un joc cu o singură urnă nu acceptă un index Joker.")
    return None


def _verify_portfolio(raw, lot, width, slots, budget, objective):
    tickets = raw["tickets"]
    if not isinstance(tickets, list) or not tickets:
        raise ValueError("Portofoliul trebuie să conțină bilete.")
    physical_tickets = []
    expanded = Counter()
    for ticket in tickets:
        if not isinstance(ticket, list) or not 1 <= len(ticket) <= lot.per_ticket:
            raise ValueError("Număr nevalid de zone într-un bilet.")
        areas = []
        for zone in ticket:
            code = _integer(zone["code"], "codul LR", 1)
            positions = _positions(zone["positions"], width, "pozițiile zonei")
            scheme = resolve_lr_scheme(lot.geometry, code, pool_size=len(positions))
            slot = _slot(zone["joker_slot"], slots)
            variants = scheme_tickets(scheme, positions)
            if len(variants) != scheme.size or len({tuple(v) for v in variants}) != scheme.size:
                raise ValueError("Schema LR nu se extinde la numărul de variante oficiale distincte.")
            expanded.update((tuple(sorted(variant)), slot) for variant in variants)
            areas.append({"code": code, "numbers": list(positions), "joker": slot + 1 if slot is not None else None})
        physical_tickets.append(areas)

    listed = Counter()
    if not isinstance(raw["paid_variants"], list):
        raise ValueError("Lista variantelor plătite este obligatorie.")
    for variant in raw["paid_variants"]:
        positions = _positions(variant["positions"], width, "pozițiile variantei")
        if len(positions) != lot.pick_n:
            raise ValueError("Lungimea unei variante plătite nu corespunde jocului.")
        listed[(positions, _slot(variant["joker_slot"], slots))] += 1
    if listed != expanded:
        raise ValueError("Variantele plătite declarate diferă de cele din schemele oficiale.")

    expected = {key: _integer(raw["metrics"][key], key) for key in METRICS}
    cost = expanded.total() * round(lot.price * 100) + FEE_BANI * len(physical_tickets)
    if expected["cost_bani"] != cost or cost > budget:
        raise ValueError("Prețul declarat nu corespunde variantelor și taxelor ori depășește bugetul.")

    # Position numbers 1..width are a canonical relabeling for enumeration.
    # The verifier checks the numerators, without authenticating an upper-bound claim.
    row = {
        "game": lot.game_id, "objective": objective, "tickets": physical_tickets, "variants": expanded.total(),
        "price_bani": cost, "budget_bani": budget, "attained": expected["attained"],
        "denominator": expected["prize_denominator"] if objective == ANY_PRIZE else expected["denominator"],
        "upper": expected["prize_denominator"] if objective == ANY_PRIZE else expected["denominator"],
    }
    try:
        measured = exact(row, tuple(range(1, width + 1)), tuple(range(1, slots + 1)))
    except AssertionError as exc:
        raise ValueError("Numărătoarea exactă nu confirmă obiectivul ori numărul de variante.") from exc
    result = {key: measured[key] for key in METRICS}
    if result != expected:
        differing = ", ".join(key for key in METRICS if result[key] != expected[key])
        raise ValueError(f"Indicatorii declarați diferă la {differing}.")
    return result


def _compare_to_incumbent(incumbent, witness, pick_n):
    savings = incumbent["cost_bani"] - witness["cost_bani"]
    if savings < 0:
        raise ValueError("Martorul depășește costul portofoliului de referință.")
    delta = {key: witness[key] - incumbent[key] for key in ("attained", "4+", "5+", "6+", "any_prize", "cost_bani")}
    protected = ("attained", *(f"{threshold}+" for threshold in range(4, pick_n + 1)))
    names = {"attained": "obiectivul selectat", "4+": "4+", "5+": "5+", "6+": "6+"}
    if regressed := [names[key] for key in protected if delta[key] < 0]:
        raise ValueError("Martorul reduce rezultatul pentru " + ", ".join(regressed) + ".")
    return {
        "classification": "lower_cost_witness" if savings else "equal_cost_witness",
        "witness": witness, "delta": delta,
        "strictly_improves": bool(savings) or any(delta[key] > 0 for key in protected),
    }


def _source_controls(body):
    rows = json.loads(body)
    if not isinstance(rows, list) or not rows:
        raise ValueError("Sursa controalelor trebuie să fie o listă de portofolii brute.")
    controls = {}
    for row in rows:
        case_id = row.get("id") if isinstance(row, dict) else None
        if not isinstance(case_id, str) or not case_id or case_id in controls:
            raise ValueError("Identificatorii din sursa brută trebuie să fie unici.")
        controls[case_id] = row
    return controls


def _verify_source_control(raw, case, lot, width, slots, budget):
    if (raw["game"] != lot.game_id or raw["objective"] != case["objective"]
            or _integer(raw["budget_bani"], "bugetul brut", 1) != budget):
        raise ValueError("Jocul, obiectivul sau bugetul din sursa brută diferă de control.")

    pool = _positions(raw["pool"], lot.max_n, "numerele pool-ului brut")
    if len(pool) != width:
        raise ValueError("Mărimea pool-ului brut diferă de control.")
    positions = {ball: position for position, ball in enumerate(pool, 1)}

    balls = raw["jokers"]
    if not isinstance(balls, list) or len(balls) != slots:
        raise ValueError("Numărul sloturilor Joker din sursa brută diferă de control.")
    if slots:
        maximum = lot.geo.second.max_n
        bonus = {_integer(ball, "bila Joker brută", 1, maximum): slot for slot, ball in enumerate(balls)}
        if len(bonus) != slots:
            raise ValueError("Bilele Joker din sursa brută trebuie să fie distincte.")
    else:
        bonus = {}

    physical = []
    if not isinstance(raw["tickets"], list):
        raise ValueError("Sursa brută trebuie să conțină o listă de bilete fizice.")
    for ticket in raw["tickets"]:
        if not isinstance(ticket, list) or not 1 <= len(ticket) <= lot.per_ticket:
            raise ValueError("Număr nevalid de zone într-un bilet din sursa brută.")
        areas = []
        for zone in ticket:
            numbers = _positions(zone["numbers"], lot.max_n, "numerele zonei brute")
            code = _integer(zone["code"], "codul LR brut", 1)
            if _integer(zone["pool_size"], "mărimea zonei brute", 1) != len(numbers):
                raise ValueError("Mărimea zonei brute diferă de numărul bilelor.")
            ball = zone["joker"]
            if slots:
                ball = _integer(ball, "bila Joker a zonei brute", 1, maximum)
                if ball not in bonus:
                    raise ValueError("Bila Joker a zonei brute lipsește din pool.")
                slot = bonus[ball]
            else:
                slot = _slot(ball, 0)
            if not set(numbers) <= positions.keys():
                raise ValueError("Zona brută conține numere din afara pool-ului.")
            areas.append({"code": code, "positions": [positions[ball] for ball in numbers], "joker_slot": slot})
        physical.append(areas)

    incumbent = case["incumbent"]
    if physical != incumbent["tickets"]:
        raise ValueError("Biletele fizice din sursa brută diferă de pozițiile sau sloturile controlului.")
    if any(_integer(raw["metrics"][key], key) != incumbent["metrics"][key] for key in METRICS):
        raise ValueError("Indicatorii din sursa brută diferă de cei ai controlului.")


def verify_witnesses(payload, archive=None):
    """Validate every case before returning any result."""
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise ValueError("Versiune necunoscută a fișierului cu portofolii.")
    source = payload["source"]
    kind = source["kind"]
    if kind not in ("incumbent_control", "user_archive"):
        raise ValueError("Sursa trebuie să fie un control al generatorului sau o arhivă a utilizatorului.")
    digest = source["sha256"]
    if not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest):
        raise ValueError("Este necesară amprenta SHA-256 a fișierului-sursă.")
    raw_controls = None
    if archive is not None:
        body = archive.read_bytes()
        if sha256(body).hexdigest() != digest:
            raise ValueError("Amprenta fișierului-sursă diferă de cea declarată.")
        if kind == "incumbent_control":
            raw_controls = _source_controls(body)

    cases = payload["cases"]
    if not isinstance(cases, list) or not cases:
        raise ValueError("Fișierul trebuie să conțină cel puțin un caz.")
    verified = []
    ids = set()
    for case in cases:
        case_id = case["id"]
        if not isinstance(case_id, str) or not case_id or case_id in ids:
            raise ValueError("Identificatorii de caz trebuie să fie unici.")
        ids.add(case_id)
        try:
            lot = require_lottery(case["game"], "RO")
            width = _integer(case["pool_size"], "mărimea pool-ului", lot.pick_n, min(lot.max_n, MAX_EXACT_POOL))
            max_slots = lot.geo.second.max_n if lot.geo.second else 0
            slots = _integer(case["joker_slots"], "numărul sloturilor Joker", int(bool(max_slots)), max_slots)
            budget = _integer(case["budget_bani"], "bugetul", 1)
            objective = case["objective"]
            if objective not in (FOUR_PLUS, ANY_PRIZE):
                raise ValueError("Obiectiv LR necunoscut.")
            incumbent = _verify_portfolio(case["incumbent"], lot, width, slots, budget, objective)
            if raw_controls is not None:
                if case_id not in raw_controls:
                    raise ValueError("Controlul lipsește din sursa brută.")
                _verify_source_control(raw_controls[case_id], case, lot, width, slots, budget)
            result = {"id": case_id, "game": lot.game_id, "classification": "incumbent_control", "incumbent": incumbent}
            candidate = case.get("witness")
            if candidate is not None:
                if kind != "user_archive":
                    raise ValueError("Controalele generatorului nu pot declara martori ai utilizatorului.")
                witness = _verify_portfolio(candidate, lot, width, slots, budget, objective)
                result.update(_compare_to_incumbent(incumbent, witness, lot.pick_n))
            verified.append(result)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"{case_id} · {exc}") from exc
    if raw_controls is not None and ids != raw_controls.keys():
        raise ValueError("Sursa brută conține portofolii fără un control corespunzător.")
    return {
        "schema_version": 1, "source": source, "source_bytes_verified": archive is not None,
        "source_portfolios_verified": raw_controls is not None, "cases": verified,
    }


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="JSON cu portofolii și numărători exacte.")
    parser.add_argument("--source", type=Path, help="Fișierul original pentru verificarea amprentei și, pentru controale, a biletelor brute.")
    args = parser.parse_args()
    try:
        result = verify_witnesses(json.loads(args.input.read_text(encoding="utf-8-sig")), args.source)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
