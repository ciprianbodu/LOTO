"""Recount all saved portfolios in the supplied November 25 LR experiment."""

from argparse import ArgumentParser
from collections import Counter
from hashlib import sha256
from itertools import combinations
import json
from math import comb
from pathlib import Path
from zipfile import ZipFile

import numpy as np

from covering.lr_budget import ANY_PRIZE, FOUR_PLUS
from covering.lr_portfolio import FEE_BANI
from covering.lr_schemes import lr_schemes
from covering.lr_selection import resolve_lr_scheme
from loto_enterprise.core.lotteries import require_lottery
from scripts.analysis.verify_lr_portfolio_witnesses import METRICS, _compare_to_incumbent


ARCHIVE_SHA256 = "54ec1fcc177cf34c0feecc1537e96e8ca9861a9480dbaf018f03e3f5b7acc51f"
SOURCE_COMMIT = "e6cad789115d22ac839fc5e9390d3838bc4c9bfb"
SOURCE_PREFIX = "loto_experiments_20261125/"
METHOD = "expanded-placement-beam12-two-zone-v1"
EVALUATOR = "independent-intersections-v1"
REPORTS = (("cover/cover_results.json", "reference", 42, ("baseline", "production", "experimental")),
           ("cover/integration_results.json", "integration", 24, ("production", "experimental")))
ROOT = Path(__file__).resolve().parents[2]


def _require(condition, label):
    if not condition:
        raise ValueError(label)


def _digest(content):
    return sha256(content).hexdigest()


def _json_digest(value):
    return _digest(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii"))


def _archive_members(archive):
    _require(_digest(archive.read_bytes()) == ARCHIVE_SHA256, "Amprenta ZIP diferă de cea a experimentului primit.")
    with ZipFile(archive) as source:
        entries = [member for member in source.infolist() if not member.is_dir()]
        names = [member.filename for member in entries]
        _require(len(names) == len(set(names)), "Arhiva conține căi duplicate.")
        manifest_path = SOURCE_PREFIX + "SHA256SUMS.json"
        manifest_data = source.read(manifest_path)
        manifest = json.loads(manifest_data)
        _require(set(names) == {manifest_path, *(SOURCE_PREFIX + name for name in manifest)},
                 "Inventarul ZIP nu corespunde manifestului.")
        findings = {}
        for name, expected in manifest.items():
            data = source.read(SOURCE_PREFIX + name)
            _require(_digest(data) == expected, f"Fișier cu amprentă diferită: {name}")
            if name in {r[0] for r in REPORTS}:
                findings[name] = json.loads(data)
    return findings, manifest, _digest(manifest_data)


def _catalog_metadata():
    geometries = {}
    for game in ("6/49", "5/40", "joker"):
        lot = require_lottery(game, "RO")
        schemes = [[s.code, s.n, s.variants] for s in sorted(lr_schemes(game), key=lambda s: s.code)]
        geometries[game] = {
            "unit_bani": round(lot.price * 100), "ticket_fee_bani": FEE_BANI,
            "per_ticket": lot.per_ticket, "draw_n": lot.draw_n, "pick_n": lot.pick_n,
            "max_n": lot.max_n, "bonus_total": lot.geo.second.max_n if lot.geo.second else 0,
            "catalog_sha256": _json_digest(schemes),
        }
    return geometries


def _catalog_matches(archived, catalog_dir):
    for name, digest in archived.items():
        body = (catalog_dir / name).read_bytes()
        _require(digest in {_digest(body), _digest(body.replace(b"\r\n", b"\n"))},
                 f"Schema oficială diferă de sursă: {name}")


def _distinct_draw_events(lot, width, variants, jokers):
    """Count unique winning (main intersection, bonus ball) events."""
    pool_masks, multiplicity = [], []
    for count in range(max(0, lot.draw_n - (lot.max_n - width)), min(width, lot.draw_n) + 1):
        outside = comb(lot.max_n - width, lot.draw_n - count)
        layer = [sum(1 << (n - 1) for n in selection) for selection in combinations(range(1, width + 1), count)]
        pool_masks.extend(layer)
        multiplicity.extend([outside] * len(layer))
    draws = np.array(pool_masks, dtype=np.uint32)
    weights = np.array(multiplicity, dtype=np.int64)
    denominator = comb(lot.max_n, lot.draw_n)
    _require(int(weights.sum()) == denominator, "Intersecțiile nu acoperă universul.")
    best = np.zeros(len(draws), dtype=np.uint8)
    by_bonus = {}
    for (positions, slot) in variants:
        ticket = sum(1 << (n - 1) for n in positions)
        hits = np.bitwise_count(draws & ticket)
        best = np.maximum(best, hits)
        if slot is not None:
            ball = jokers[slot]
            occurs = hits > 0
            by_bonus[ball] = occurs | by_bonus[ball] if ball in by_bonus else occurs
    tails = {f"{threshold}+": int(weights[best >= threshold].sum()) for threshold in (4, 5, 6)}
    if lot.geo.second:
        maximum = lot.geo.second.max_n
        unassisted = best >= 3
        prizes = sum(int(weights[unassisted | by_bonus.get(ball, False)].sum()) for ball in range(1, maximum + 1))
        prize_denominator = denominator * maximum
    else:
        prizes = int(weights[best >= (3 if lot.game_id == "6/49" else 4)].sum())
        prize_denominator = denominator
    if lot.pick_n == lot.draw_n == 6:
        _require(tails["6+"] == len(variants), "Numărătorul 6/6 diferă de variantele distincte.")
    return {**tails, "denominator": denominator, "any_prize": prizes, "prize_denominator": prize_denominator}


def _recount(case, raw, declared):
    lot = require_lottery(case["game"], "RO")
    pool, jokers = case["pool"], case["jokers"]
    slots = {ball: slot for slot, ball in enumerate(jokers)}
    positions = {ball: position for position, ball in enumerate(pool, 1)}
    _require(pool == sorted(set(pool)) and all(type(n) is int and 1 <= n <= lot.max_n for n in pool),
             "Pool-ul brut are numere nevalide.")
    if lot.geo.second:
        _require(jokers and len(slots) == len(jokers)
                 and all(type(n) is int and 1 <= n <= lot.geo.second.max_n for n in jokers), "Pool Joker nevalid.")
    else:
        _require(not jokers, "Sloturi bonus în joc fără urnă bonus.")
    objective = case["objective"]
    _require(objective in (FOUR_PLUS, ANY_PRIZE), "Obiectiv LR necunoscut.")
    _require(all(raw[key] == case[key] for key in ("game", "objective", "budget_bani")), "Identitatea portofoliului diferă.")
    all_tickets = []
    paid = Counter()
    codes = []
    for ticket in raw["tickets"]:
        _require(isinstance(ticket, list) and 1 <= len(ticket) <= lot.per_ticket, "Grupare fizică nevalidă.")
        placed = []
        for zone in ticket:
            numbers = zone["numbers"]
            _require(numbers == sorted(set(numbers)) and all(type(n) is int and n in positions for n in numbers),
                     "Amplasare nevalidă în pool.")
            _require(zone["pool_size"] == len(numbers), "Mărimea zonei diferă de numărul bilelor.")
            scheme = resolve_lr_scheme(lot.geometry, zone["code"], pool_size=len(numbers))
            mapped = [positions[ball] for ball in numbers]
            if lot.geo.second:
                _require(type(zone["joker"]) is int and zone["joker"] in slots, "Bila Joker a zonei lipsește din pool.")
                slot = slots[zone["joker"]]
            else:
                _require(zone["joker"] is None, "Bilă Joker într-un joc fără urnă bonus.")
                slot = None
            _require(len(set(scheme.variants)) == scheme.size, "Rânduri oficiale duplicate în schemă.")
            paid.update((tuple(sorted(mapped[index - 1] for index in variant)), slot) for variant in scheme.variants)
            placed.append({"code": scheme.code, "positions": mapped, "joker_slot": slot})
            codes.append(scheme.code)
        all_tickets.append(placed)

    costs = paid.total() * round(lot.price * 100) + FEE_BANI * len(all_tickets)
    _require(paid and paid.total() == raw["variants"], "Lista expandată are alt număr de variante plătite.")
    _require(costs == raw["price_bani"] and costs <= case["budget_bani"], "Costul brut diferă de costul recalculat.")
    metrics = _distinct_draw_events(lot, len(pool), paid, jokers)
    metrics.update(
        attained=metrics["any_prize" if objective == ANY_PRIZE else "4+"], cost_bani=costs,
        zones=len(codes), physical_tickets=len(all_tickets), duplicate_paid_variants=paid.total() - len(paid),
    )
    denominator = metrics["prize_denominator" if objective == ANY_PRIZE else "denominator"]
    _require(metrics["attained"] == raw["attained"] and denominator == raw["denominator"],
             "Obiectivul sau numitorul brut diferă.")
    _require(declared["codes"] == codes, "Codurile oficiale declarate diferă de cele din bilete.")
    for key in METRICS:
        _require(metrics[key] == declared[key], f"Indicatorul {key} diferă: {metrics[key]} față de {declared[key]}.")
    return {
        "tickets": all_tickets, "metrics": metrics,
        "paid_variants": [{"positions": list(variant), "joker_slot": slot} for variant, slot in paid.elements()],
    }


def _deltas(after, before):
    return {
        "objective_gain": after["attained"] - before["attained"],
        "cost_change_bani": after["cost_bani"] - before["cost_bani"],
        "tail_gains": {key: after[key] - before[key] for key in ("4+", "5+", "6+")},
    }


def verify_archive(archive):
    reports, manifest, manifest_sha256 = _archive_members(archive)
    grid, integration = (reports[r[0]] for r in REPORTS)
    harness = manifest["cover/run_cover_experiment.py"]
    _require(harness == grid["source_sha256"]["experiment_harness"] == integration["candidate_sha256"],
             "Cele două măsurători nu leagă același prototip.")
    _catalog_matches(grid["source_sha256"]["catalog_files"], ROOT / "covering_designs/lr")

    verified, certificates, summary = [], [], {}
    for member, group, count, labels in REPORTS:
        report = reports[member]
        _require(report["protocol"]["source_commit"] == SOURCE_COMMIT and len(report["cases"]) == count,
                 "Revizia sau mărimea lotului nu corespunde experimentului.")
        _require(report["protocol_sha256"] == manifest[
            "cover/protocol.json" if group == "reference" else "cover/integration_protocol.json"
        ], "Amprenta protocolului diferă.")
        scope_summary = Counter()
        for case in report["cases"]:
            raw_id = case["id"]
            try:
                snapshots = {label: _recount(case, case[label]["witness"], case[label]["verified"]) for label in labels}
                incumbent = snapshots["production"]
                witness = snapshots["experimental"]
                before, after = incumbent["metrics"], witness["metrics"]
                lot = require_lottery(case["game"], "RO")
                _compare_to_incumbent(before, after, lot.pick_n)
                delta = _deltas(after, before)
                _require(delta == case["production_to_experimental"] and delta["cost_change_bani"] == 0,
                         "Diferențele sau costul perechii diferă de rezultatul publicat.")
                if "baseline" in snapshots:
                    older = _deltas(before, snapshots["baseline"]["metrics"])
                    _require(older == case["baseline_to_production"], "Diferențele față de baza veche diferă.")
                    scope_summary["baseline_to_production_improvements"] += older["objective_gain"] > 0
            except (ValueError, KeyError, TypeError) as exc:
                raise ValueError(f"{group} · {raw_id} · {exc}") from exc
            context = {
                "game": case["game"], "objective": case["objective"], "budget_bani": case["budget_bani"],
                "pool_size": len(case["pool"]), "joker_slots": len(case["jokers"]),
                "source_member": member, "source_case_id": raw_id,
            }
            verified.append({
                "id": f"{group}:{raw_id}", **context, "pool": case["pool"], "jokers": case["jokers"],
                **({"baseline": snapshots["baseline"]} if "baseline" in snapshots else {}),
                "incumbent": incumbent, "witness": witness, "production_to_experimental": delta,
            })
            certificates.append({
                **context, "incumbent_cost_bani": before["cost_bani"],
                "incumbent_sha256": _json_digest(incumbent["tickets"]),
                "tickets": witness["tickets"], "metrics": after,
            })
            scope_summary["cases"] += 1
            scope_summary["portfolios"] += len(labels)
            scope_summary["objective_improvements"] += delta["objective_gain"] > 0
        _require(len({case["id"] for case in verified}) == len(verified), "Cazuri duplicate.")
        summary[group] = dict(scope_summary)

    _require(summary == {
        "reference": {"baseline_to_production_improvements": 4, "cases": 42, "portfolios": 126, "objective_improvements": 8},
        "integration": {"cases": 24, "portfolios": 48, "objective_improvements": 7},
    }, "Totalurile recalculării diferă de cele declarate.")
    source = {
        "kind": "user_archive", "name": "Teste_LOTO_2026_11_25.zip", "sha256": ARCHIVE_SHA256,
        "commit": SOURCE_COMMIT, "manifest_sha256": manifest_sha256, "candidate_sha256": harness,
        "members": {member: manifest[member] for member, *_ in REPORTS},
    }
    details = {"schema_version": 1, "source": source, "evaluator": EVALUATOR, "summary": summary, "cases": verified}
    compact = {
        "schema_version": 1, "method": METHOD, "evaluator": EVALUATOR, "archive_sha256": ARCHIVE_SHA256,
        "source_commit": SOURCE_COMMIT, "candidate_sha256": harness,
        "geometries": _catalog_metadata(), "certificates": certificates,
    }
    return details, compact


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, help="Arhiva originală Teste_LOTO_2026_11_25.zip.")
    parser.add_argument("--witnesses", type=Path, help="Fișierul versionat care trebuie confruntat cu ZIP-ul.")
    parser.add_argument("--certificates", type=Path, help="Șabloanele compacte care trebuie confruntate cu ZIP-ul.")
    parser.add_argument("--write", action="store_true", help="Scrie explicit artefactele recalculate la căile indicate.")
    args = parser.parse_args()
    try:
        details, compact = verify_archive(args.archive)
        for path, result in ((args.witnesses, details), (args.certificates, compact)):
            if path is None:
                continue
            if args.write:
                path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            else:
                _require(json.loads(path.read_text(encoding="utf-8")) == result, f"Artefactul diferă de arhivă: {path}")
    except (OSError, KeyError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({"source": details["source"], "summary": details["summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
