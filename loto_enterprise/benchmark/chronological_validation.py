"""Report the chronological comparison for a completed Re-Bench."""

import logging

from loto_enterprise.core.lotteries import games_for_country, normalize_country
from runtime_paths import RUNTIME_ROOT
from ui_shared import atomic_write_json

VALIDATION_CODES_ENV = "LOTO_BENCH_VALIDATION_CODES"
PROGRESS_MARKER = "[VALIDARE CRONOLOGICĂ]"

# Prefer fewer reselections at exact ties, with the fixed baseline first.
CAUSAL_POLICIES = (
    "frequency",
    "pool_frozen",
    "decision_frozen",
    "ticket_frozen",
    "pool_block26",
    "decision_block26",
    "ticket_block26",
    "pool_daily",
    "ticket_daily",
)


def select_best(arms: dict, *, second_urn: bool = False) -> str:
    """Maximize draws with 4+ in the final pool, or correct single-ball picks for Urna 2."""
    metric = "primary" if second_urn else "pool_4plus"
    return max(
        (key for key in CAUSAL_POLICIES if key in arms),
        key=lambda key: arms[key][metric],
    )


def validation_path(country: str):
    return (
        RUNTIME_ROOT / "chronological_validation" / f"{normalize_country(country)}.json"
    )


def run_validation(country: str, run_id: str, scheme_codes: dict) -> None:
    """Evaluate only the country benchmarked, using its launch-time LR codes."""
    from loto_enterprise.benchmark.methods import require_complete_catalog
    from scripts.analysis.nested_wfv_2026_11_18 import study_game

    # A shared catalog failure invalidates the entire run. Keep the previous
    # country report, just as we do for invalid launch settings.
    require_complete_catalog(country=country)
    streams = {
        key: key == lot.bench_key_urna2
        for lot in games_for_country(country)
        for key in lot.bench_keys
    }
    if not isinstance(scheme_codes, dict) or scheme_codes.keys() != streams.keys():
        raise ValueError("Setările validării nu corespund jocurilor din Re-Bench.")

    logger = logging.getLogger(__name__)
    results, errors = {}, {}
    for index, (key, second_urn) in enumerate(streams.items(), start=1):
        logger.info(
            "%s Se calculează %s (%d/%d)", PROGRESS_MARKER, key, index, len(streams)
        )
        try:
            result = study_game(
                key, scheme_code=scheme_codes[key], hit_target=4, selection_basis="pool",
                active_catalog=True,
            )
            result["selection_metric"] = "top1" if second_urn else "pool_4plus"
            result["best_policy"] = select_best(result["arms"], second_urn=second_urn)
            results[key] = result
        except Exception as exc:
            logger.exception("Validarea cronologică a eșuat pentru %s", key)
            errors[key] = str(exc)

    atomic_write_json(
        validation_path(country),
        {"run_id": run_id, "results": results, "errors": errors},
    )
    logger.info(
        "%s Încheiată (%d/%d jocuri sau urne validate)",
        PROGRESS_MARKER,
        len(results),
        len(streams),
    )
