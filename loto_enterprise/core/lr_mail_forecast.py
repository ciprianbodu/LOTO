"""Prepare the inverse 5–40 pool accompanying a completed job's normal numbers."""

from __future__ import annotations

import logging
from pathlib import Path

from covering.lr_budget import DEFAULT_BUDGET_LEI, FOUR_PLUS
from covering.lr_pool_ranking import LR_RESTRICTED_RANGE
from loto_enterprise.core.lr_budget_forecast import recommend_playable_games


logger = logging.getLogger(__name__)


def prepare_inverse_mail_pools(
    record, source, *, budget_lei=DEFAULT_BUDGET_LEI, objective=FOUR_PLUS,
    dynamic=True, lookback=100, joker_size=1, hit_target=4,
):
    """Read detached job settings and exclude exactly the original pool shown in its email."""
    lot, data, filename = record["spec"], record["data"], record["fname"]
    if not lot.is_romanian or not data.get("hard_core"):
        return {}

    audit = data.get("audit") or {}
    budget_lei = data.get("lr_budget_lei", budget_lei)
    objective = data.get("lr_budget_objective", objective)
    alternative = {"budget_lei": budget_lei, "objective": objective, "number_range": LR_RESTRICTED_RANGE}
    # A canonical file can replace a missing detached frame only if its full
    # content hash matches the generating job. The budget forecaster verifies it.
    if source is None and not (filename == Path(lot.csv).name and audit.get("hash")):
        alternative["error"] = f"{filename} · Istoricul original al generării este indisponibil."
        return {"inverse_reduced": alternative}

    saved_lookback = data.get("lookback")
    if saved_lookback is None:
        saved_lookback = audit.get("lookback_pct")
    if saved_lookback is not None:
        lookback = saved_lookback
    main_decision = (audit.get("bench_winner") or {}).get(lot.bench_key) or {}
    if data.get("dynamic_method") is not None:
        dynamic = data["dynamic_method"]
    elif audit.get("dynamic_selection") or main_decision:
        dynamic = bool(audit.get("dynamic_selection") or main_decision.get("dynamic_selection"))
    bonus = data.get("joker_pool") or data.get("hard_core_joker") or ()
    try:
        plans = recommend_playable_games(
            budget_lei, objective, lot.game_id, histories={lot.game_id: (filename, source)} if source is not None else {},
            generated={lot.game_id: (filename, data)}, pool_codes={}, dynamic=dynamic, lookback=lookback,
            joker_size=len(bonus) or joker_size, hit_target=data.get("bench_hit_target", hit_target),
            inverse=True, number_range=LR_RESTRICTED_RANGE, exclude_generated_pool=True,
        )
        if not plans:
            raise ValueError("Nicio schemă oficială nu încape în buget.")
        plan = plans[0]
        if not plan["tickets"] or plan.get("forecast_error"):
            raise ValueError(plan.get("forecast_error") or "Pool-ul invers 5–40 nu poate fi completat.")
        alternative.update(plan["forecast"], tickets=plan["tickets"], price_bani=plan["price_bani"], variants=plan["variants"])
    except (ValueError, OSError) as exc:
        alternative["error"] = str(exc)
    except Exception:
        logger.exception("Calculul pool-ului invers 5–40 pentru e-mail a eșuat (%s)", filename)
        alternative["error"] = "Calculul pool-ului invers 5–40 a eșuat."
    return {"inverse_reduced": alternative}