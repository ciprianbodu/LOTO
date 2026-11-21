"""Fill budget tickets from a pool ranked by the production selector."""

from __future__ import annotations

import logging
from copy import deepcopy
from fractions import Fraction
from functools import lru_cache
from pathlib import Path

import numpy as np

from covering.lr_budget import recommend_games
from covering.lr_pool_budget import required_pool_size, restrict_to_pool
from covering.lr_selection import lr_pool_sizes, resolve_lr_scheme, select_lr_scheme
from covering.probability import _integer
from loto_engine import LotoEngine
from loto_enterprise.core.dynamic_winner import choose_dynamic_winner
from loto_enterprise.core.history import history_dates, training_cutoffs
from loto_enterprise.core.lotteries import require_lottery
from loto_enterprise.core.ranking import rank_by_score


_ROOT = Path(__file__).resolve().parents[2]
logger = logging.getLogger(__name__)


@lru_cache(maxsize=32)
def _dynamic_scores(game, code, draws, cutoffs):
    lot = require_lottery(game, "RO")
    matrix = np.frombuffer(draws, dtype=np.int32).reshape(-1, lot.draw_n)
    return choose_dynamic_winner(matrix, cutoffs, resolve_lr_scheme(game, code))


def _preview_pool(lot, source, scheme, lookback, dynamic, joker_size):
    engine = LotoEngine(lot.geometry, game_key=lot.bench_key, country=lot.country)
    if source is None:
        if not engine.load_data(str(_ROOT / lot.csv)):
            raise ValueError("Istoricul jocului nu poate fi încărcat.")
    else:
        engine.data = source[1]
        engine._build_draw_matrix()
    if engine._draw_matrix is None or not len(engine._draw_matrix):
        raise ValueError("Istoricul jocului nu conține extrageri valide.")
    if 0 < lookback < 100:
        rows = max(1, int(len(engine.data) * (lookback / 100)))
        engine.data = engine.data.tail(rows)
        engine._draw_matrix = engine._draw_matrix[-rows:]
    engine._winner_pool_hint = scheme.n
    if dynamic:
        scores, selection = _dynamic_scores(
            lot.game_id, scheme.code, engine._draw_matrix.tobytes(), training_cutoffs(engine.data),
        )
    else:
        scores = engine._get_timesfm_scores()
        selection = engine.audit.get("bench_winner", {}).get(
            lot.bench_key, {"method": "frequency", "fallback": True},
        )
    pool = engine._get_timesfm_pool(scores, scheme.n, set())
    jokers, bonus = (), None
    if lot.bench_key_urna2 and engine._valid_joker_values().size:
        bonus_scores = engine._get_timesfm_scores(is_joker_drum=True)
        maximum = lot.geo.second.max_n
        ranked = rank_by_score({n: s for n, s in bonus_scores.items() if 1 <= n <= maximum}, joker_size)
        if not ranked:
            ranked = engine._get_hard_core_joker(engine.analyze_joker_frequency(), pool_size=joker_size)
        jokers = tuple(ranked)
        bonus = engine.audit.get("bench_winner", {}).get(
            lot.bench_key_urna2, {"method": "frequency", "fallback": True},
        )
    dates = history_dates(engine.data)
    return {
        "pool": pool, "jokers": jokers, "selection": deepcopy(selection), "joker_selection": bonus,
        "date": dates.iloc[-1].strftime("%d-%m-%Y") if dates is not None else None,
        "rows": len(engine.data), "code": scheme.code, "dynamic": dynamic, "generated": False,
    }


def _generated_pool(lot, result):
    audit = result.get("audit") or {}
    winners = audit.get("bench_winner") or {}
    main = winners.get(lot.bench_key) or {}
    selection = audit.get("dynamic_selection") or main.get("dynamic_selection")
    days = (result.get("context") or {}).get("last_3") or []
    date = days[-1].get("date") if days else None
    return {
        "pool": result.get("hard_core"),
        "jokers": result.get("joker_pool") or result.get("hard_core_joker") or (),
        "selection": selection or main,
        "joker_selection": winners.get(lot.bench_key_urna2),
        "date": date if date and date != "N/A" else None,
        "rows": result.get("total_draws") or 0,
        "code": result.get("lr_scheme_code") or (audit.get("lr_scheme") or {}).get("code"),
        "dynamic": bool(selection), "generated": True,
    }


def _balls(values, maximum):
    balls = tuple(_integer(n) for n in (values or ()))
    if len(set(balls)) != len(balls) or any(not 1 <= n <= maximum for n in balls):
        raise ValueError("Pool-ul conține numere duplicate sau în afara urnei.")
    return balls


def recommend_playable_games(
    budget_lei, objective, game_id=None, histories=None, *,
    pool_codes, generated=None, lookback=100, dynamic=True, joker_size=5,
):
    """Plan by budget, then use the production pool or score the largest LR pool."""
    plans = recommend_games(budget_lei, objective, game_id)
    for plan in plans:
        lot = require_lottery(plan["game"], "RO")
        result = (generated or {}).get(lot.game_id)
        source = (histories or {}).get(lot.game_id)
        source_name = source[0] if source else Path(lot.csv).name
        filename = result[0] if result else source_name
        try:
            saved = result[1] if result else {}
            if result:
                forecast = _generated_pool(lot, saved)
                size = len(forecast["pool"] or ())
                dynamic_mode = saved.get("dynamic_method", forecast["dynamic"] or dynamic)
            else:
                scheme = resolve_lr_scheme(lot.geometry, pool_codes[lot.game_id])
                size = scheme.n
                dynamic_mode = dynamic
            maximum = max(lr_pool_sizes(lot.geometry))
            if size < min(maximum, required_pool_size(plan)):
                largest = select_lr_scheme(lot.geometry, maximum)
                filename = source_name
                saved_lookback = saved.get("lookback")
                expanded = _preview_pool(
                    lot, source, largest, lookback if saved_lookback is None else float(saved_lookback),
                    dynamic_mode, joker_size,
                )
                if result:
                    expanded.update(jokers=forecast["jokers"], joker_selection=forecast["joker_selection"])
                forecast = expanded
                forecast["expanded_from"] = size
            elif not result:
                forecast = _preview_pool(lot, source, scheme, lookback, dynamic, joker_size)
            pool = _balls(forecast["pool"], lot.max_n)
            if not pool:
                raise ValueError("Pool-ul jocului este gol.")
            jokers = ()
            if lot.bench_key_urna2:
                jokers = _balls(forecast["jokers"], lot.geo.second.max_n)
                if not jokers:
                    raise ValueError("Pool-ul Urnei 2 Joker este indisponibil.")
            forecast.update(filename=filename, pool=sorted(pool), jokers=jokers)
            if not restrict_to_pool(plan, objective, pool, jokers)["tickets"]:
                plan["forecast_error"] = f"Niciun cod LR în buget pentru pool-ul de {len(pool)} numere."
            plan["forecast"] = forecast
        except (OSError, ValueError) as exc:
            logger.warning("Sugestie LR %s (%s): %s", lot.game_id, filename, exc)
            plan["forecast_error"] = f"{filename} · {exc}"
        except Exception:
            logger.exception("Sugestie LR pentru %s", lot.game_id)
            plan["forecast_error"] = f"{filename} · Calculul biletelor a eșuat."
        if "forecast" not in plan:
            plan.update(tickets=[], variants=0, price_bani=0, attained=0, upper=None, optimal=False)
    return sorted(
        plans,
        key=lambda item: (
            not item["tickets"], -Fraction(item["attained"], item["denominator"]),
            item["price_bani"], item["game"],
        ),
    )
