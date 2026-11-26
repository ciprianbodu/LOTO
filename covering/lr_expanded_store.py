"""A validated LR position recipe shared by workers, exclusively within its geometry."""

from hashlib import sha256
import json
import logging

from process_lock import file_lock


_logger = logging.getLogger(__name__)


def _tickets(zones, capacity):
    rows = [{"code": code, "positions": [p + 1 for p in positions], "joker_slot": slot}
            for code, positions, _, slot in zones]
    return [rows[i:i + capacity] for i in range(0, len(rows), capacity)]


def incumbent_hash(zones, capacity):
    canonical = json.dumps(_tickets(zones, capacity), sort_keys=True, separators=(",", ":")).encode("ascii")
    return sha256(canonical).hexdigest()


def address(game, width, objective, budget, price, slots, threshold, upper, zones, score, floor,
            duplicates, tariff):
    from covering import lr_expanded_templates as templates
    from covering.lr_expanded_search import ALGORITHM_VERSION, EVALUATOR_VERSION
    from runtime_paths import WF_CACHE_DIR

    # JSON primitives keep the identity identical after a read in a new process.
    context = {
        "schema": 1, "game": game, "pool_size": width, "objective": objective,
        "budget_bani": budget, "cost_cap_bani": price, "joker_slots": slots, "threshold": threshold,
        "upper": upper, "incumbent_sha256": incumbent_hash(zones, tariff[2]), "score": score,
        "tails": dict(floor), "duplicates": duplicates, "tariff_and_geometry": list(tariff),
        "catalog_sha256": templates._catalog_hash(game), "rules_sha256": templates._rules(),
        "method": ALGORITHM_VERSION, "evaluator": EVALUATOR_VERSION,
    }
    key = sha256(json.dumps(context, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()
    return WF_CACHE_DIR / "lr_expanded" / (key + ".json"), context


def verified(path, context, record):
    from covering import lr_expanded_templates as templates
    from loto_enterprise.core.lotteries import require_lottery

    if record.get("context") != context:
        raise ValueError("Identitatea rețetei LR diferă de calculul curent.")
    lot = require_lottery(context["game"], "RO")
    width, slots, objective = context["pool_size"], context["joker_slots"], context["objective"]
    zones = templates._unpack(record, lot, width, slots)
    metrics = templates._measured(lot.game_id, width, objective, slots, zones,
                                  tuple(context["tariff_and_geometry"]))
    score, cost = metrics["attained"], metrics["cost_bani"]
    if (metrics != record["metrics"] or not context["score"] <= score <= context["upper"]
            or cost > context["cost_cap_bani"]
            or any(metrics[f"{t}+"] < v for t, v in context["tails"].items())
            or (score == context["score"] and cost == context["cost_cap_bani"]
                and metrics["duplicate_paid_variants"] > context["duplicates"])):
        raise ValueError("Rețeta salvată nu protejează portofoliul plătit.")
    report = {
        "candidate": context["method"], "certificate": "proven_bound" if score == context["upper"] else "feasible",
        "protected_numerators": {t: metrics[f"{t}+"] for t in context["tails"]},
        "duplicate_paid_variants": metrics["duplicate_paid_variants"],
        "expanded_local_certificate": {"key": path.stem, "incumbent_sha256": context["incumbent_sha256"],
                                       "evaluator": context["evaluator"]},
    }
    return zones, score, cost, report


def load(path, context):
    try:
        return verified(path, context, json.loads(path.read_bytes()))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError, KeyError, IndexError):
        _logger.warning("Rețeta LR salvată nu poate fi revalidată: %s", path)
        return None


def publish(path, context, result):
    """Racing workers adopt the first fully certified recipe for this exact key."""
    from covering import lr_expanded_templates as templates
    from ui_shared import atomic_write_json

    zones, score, price, report = result
    tariff = tuple(context["tariff_and_geometry"])
    metrics = templates._measured(context["game"], context["pool_size"], context["objective"],
                                  context["joker_slots"], zones, tariff)
    record = {"context": context, "tickets": _tickets(zones, tariff[2]), "metrics": metrics}
    measured = verified(path, context, record)
    if measured[1:3] != (score, price):
        raise ValueError("Verificarea independentă a soluției LR diferă de căutare.")
    try:
        with file_lock(path, timeout=0.3):
            previous = load(path, context)
            if previous is not None:
                return previous
            atomic_write_json(path, record, indent=None)
    except OSError:
        _logger.warning("Rețeta LR verificată nu a putut fi memorată: %s", path)
    return zones, score, price, report | measured[3]
