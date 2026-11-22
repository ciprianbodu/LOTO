"""Fill budget tickets from a pool ranked by the production selector."""

from __future__ import annotations

import io
import logging
from copy import deepcopy
from fractions import Fraction
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from covering.lr_budget import recommend_games
from covering.lr_pool_budget import required_pool_size, restrict_to_pool
from covering.lr_selection import lr_pool_sizes, resolve_lr_scheme, select_lr_scheme
from covering.probability import _integer
from loto_engine import LotoEngine
from loto_enterprise.benchmark.restricted_decision import restricted_bench_scores
from loto_enterprise.core.draw_validation import valid_draw_matrix
from loto_enterprise.core.dynamic_winner import choose_dynamic_winner
from loto_enterprise.core.history import history_dates, training_cutoffs
from loto_enterprise.core.lotteries import require_lottery
from loto_enterprise.core.ranking import rank_by_score


_ROOT = Path(__file__).resolve().parents[2]
logger = logging.getLogger(__name__)


def budget_plan_scheme(plan):
    """Official scoring scheme for the full set of positions the budget needs."""
    game = plan["game"]
    zones = [zone for ticket in plan["tickets"] for zone in ticket]
    if not zones:
        raise ValueError("Bugetul nu permite nicio schemă LR pentru acest joc.")
    if len(zones) == 1:
        return resolve_lr_scheme(game, zones[0]["code"], pool_size=zones[0]["pool_size"])
    required = required_pool_size(plan)
    sizes = lr_pool_sizes(game)
    size = next((width for width in sizes if width >= required), sizes[-1])
    return select_lr_scheme(game, size)


@lru_cache(maxsize=32)
def _dynamic_scores(game, code, draws, cutoffs, excluded_numbers=()):
    lot = require_lottery(game, "RO")
    matrix = np.frombuffer(draws, dtype=np.int32).reshape(-1, lot.draw_n)
    return choose_dynamic_winner(
        matrix, cutoffs, resolve_lr_scheme(game, code), excluded_numbers=excluded_numbers,
    )


def _forecast_engine(lot, source, lookback, expected_hash=None):
    engine = LotoEngine(lot.geometry, game_key=lot.bench_key, country=lot.country)
    if source is None:
        loaded = engine.load_data(str(_ROOT / lot.csv))
    else:
        # Replay the worker's index-free CSV serialization, including dtype inference,
        # chronological normalization and the content hash of the complete valid history.
        with io.StringIO(source[1].to_csv(index=False)) as stream:
            loaded = engine.load_data(stream)
    if not loaded:
        raise ValueError("Istoricul jocului nu conține extrageri valide.")
    if expected_hash and engine.audit["hash"] != expected_hash:
        raise ValueError("Istoricul disponibil diferă de cel al pool-ului generat. Regenerați pool-ul.")
    if 0 < lookback < 100:
        rows = max(1, int(len(engine.data) * (lookback / 100)))
        engine.data = engine.data.tail(rows)
        engine._draw_matrix = engine._draw_matrix[-rows:]
    return engine


def _check_capacity(name, maximum, count, excluded):
    remaining = maximum - len(excluded)
    if remaining < count:
        raise ValueError(
            f"{name}: rămân {remaining} numere eligibile după excluderea celor "
            f"{len(excluded)} din pool-ul normal. Sunt necesare {count}."
        )


def _score_pool(lot, engine, scheme, dynamic, joker_size, *, excluded=(), excluded_jokers=(), hit_target=None):
    engine._winner_pool_hint = scheme.n
    if dynamic:
        scores, selection = _dynamic_scores(
            lot.game_id, scheme.code, engine._draw_matrix.tobytes(), training_cutoffs(engine.data), excluded,
        )
    elif excluded:
        scores, selection = restricted_bench_scores(
            engine._draw_matrix, training_cutoffs(engine.data), lot.bench_key, scheme.n, excluded, hit_target,
        )
    else:
        scores = engine._get_timesfm_scores()
        selection = engine.audit.get("bench_winner", {}).get(
            lot.bench_key, {"method": "frequency", "fallback": True},
        )
    pool = engine._get_timesfm_pool(scores, scheme.n, set(excluded))
    if len(pool) != scheme.n or not set(pool).isdisjoint(excluded):
        raise ValueError(f"Pool-ul eligibil pentru Cod {scheme.code} nu poate fi completat.")
    jokers, bonus = (), None
    if lot.bench_key_urna2:
        maximum = lot.geo.second.max_n
        if excluded_jokers:
            matrix, valid = valid_draw_matrix(engine.data, ["joker"], draw_n=1, max_num=maximum)
            if not len(matrix):
                raise ValueError("Istoricul Urnei 2 Joker nu conține extrageri valide.")
            bonus_scores, bonus = restricted_bench_scores(
                matrix, training_cutoffs(engine.data.loc[valid]), lot.bench_key_urna2, 1, excluded_jokers, hit_target,
            )
            jokers = tuple(rank_by_score({
                n: score for n, score in bonus_scores.items() if 1 <= n <= maximum and n not in excluded_jokers
            }, joker_size))
            if len(jokers) != joker_size:
                raise ValueError("Pool-ul eligibil al Urnei 2 Joker nu poate fi completat.")
        elif engine._valid_joker_values().size:
            bonus_scores = engine._get_timesfm_scores(is_joker_drum=True)
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
    context = result.get("context") or {}
    days = context.get("last_3") or []
    date = days[-1].get("date") if days else None
    return {
        "pool": result.get("hard_core"),
        "jokers": result.get("joker_pool") or result.get("hard_core_joker") or (),
        "selection": selection or main,
        "joker_selection": winners.get(lot.bench_key_urna2),
        "date": date if date and date != "N/A" else None,
        "rows": result.get("total_draws") or 0,
        "code": (result.get("lr_scheme_code") or (audit.get("lr_scheme") or {}).get("code")
                 or context.get("lr_scheme_code") or (selection or {}).get("scheme_code")),
        "dynamic": bool(selection), "generated": True,
    }


def _balls(values, maximum):
    balls = tuple(_integer(n) for n in (values or ()))
    if len(set(balls)) != len(balls) or any(not 1 <= n <= maximum for n in balls):
        raise ValueError("Pool-ul conține numere duplicate sau în afara urnei.")
    return balls


def _number_range(limits):
    if limits is None:
        return None
    if not isinstance(limits, (list, tuple)) or len(limits) != 2:
        raise ValueError("Intervalul numerelor principale necesită două limite întregi.")
    low, high = map(_integer, limits)
    if low > high:
        raise ValueError("Limita inferioară a intervalului depășește limita superioară.")
    return low, high


def _range_exclusions(lot, scheme, number_range, normal_pool=()):
    """Fixed main-urn exclusions shared by historical ranking and the next draw."""
    blocked = set(normal_pool)
    if number_range is None:
        _check_capacity(lot.name, lot.max_n, scheme.n, blocked)
    else:
        low, high = number_range
        blocked.update(n for n in range(1, lot.max_n + 1) if not low <= n <= high)
        eligible = lot.max_n - len(blocked)
        if eligible < scheme.n:
            detail = " după excluderea pool-ului normal" if normal_pool else ""
            raise ValueError(
                f"În intervalul {low}–{high} rămân {eligible} numere eligibile{detail}. "
                f"Cod {scheme.code} necesită {scheme.n}."
            )
    return tuple(sorted(blocked))


def _inverse_forecast(lot, engine, scheme, dynamic, normal_pool, normal_jokers, hit_target, number_range):
    normal = tuple(sorted(normal_pool))
    blocked = _range_exclusions(lot, scheme, number_range, normal)
    excluded_jokers = tuple(sorted(normal_jokers))
    if lot.bench_key_urna2:
        _check_capacity("Urna 2 Joker", lot.geo.second.max_n, len(normal_jokers), excluded_jokers)
    forecast = _score_pool(
        lot, engine, scheme, dynamic, len(normal_jokers), excluded=blocked,
        excluded_jokers=excluded_jokers, hit_target=hit_target,
    )
    forecast.update(excluded_pool=list(normal), eligible_count=lot.max_n - len(blocked), inverse_range=number_range)
    if lot.bench_key_urna2:
        forecast.update(excluded_jokers=excluded_jokers, eligible_joker_count=lot.geo.second.max_n - len(excluded_jokers))
    return forecast


def _budget_forecast(plan, lot, requested_code, result, source, lookback, dynamic, joker_size, inverse, hit_target,
                     number_range):
    scheme = budget_plan_scheme(plan)
    number_range = _number_range(number_range)
    filename = source[0] if source else Path(lot.csv).name
    saved = result[1] if result else {}
    previous = _generated_pool(lot, saved) if result else None
    saved_lookback = saved.get("lookback", (saved.get("audit") or {}).get("lookback_pct"))
    same_lookback = saved_lookback is None or (float(saved_lookback) or 100) == (lookback or 100)
    if previous:
        size = len(previous["pool"] or ())
        code = previous["code"] or requested_code
        saved_mode = saved.get("dynamic_method", previous["dynamic"] if previous["selection"] else dynamic)
        same_settings = (
            same_lookback and saved_mode == dynamic
            and (not lot.bench_key_urna2 or len(previous["jokers"]) == joker_size)
        )
    else:
        code = requested_code
        size = resolve_lr_scheme(lot.geometry, code).n if code is not None else scheme.n
        same_settings = False

    # A dynamic winner is specific to the exact LR layout, even at equal width.
    same_scheme = str(code) == str(scheme.code) or (code is None and not dynamic)
    reuse = same_settings and size == scheme.n and same_scheme
    engine = None
    if not reuse or inverse or number_range is not None:
        if result and filename != result[0]:
            raise ValueError("Lipsește istoricul folosit la generarea acestui pool.")
        expected_hash = (saved.get("audit") or {}).get("hash")
        engine = _forecast_engine(lot, source, lookback, expected_hash)
        if previous and not expected_hash:
            dates = history_dates(engine.data)
            if same_lookback and previous["rows"] and previous["rows"] != len(engine.data):
                raise ValueError("Numărul de extrageri diferă de istoricul pool-ului generat. Regenerați pool-ul.")
            if previous["date"]:
                date = history_dates(pd.DataFrame({"date": [previous["date"]]})).iloc[0]
                if dates is None or date != dates.iloc[-1]:
                    raise ValueError("Ultima extragere diferă de istoricul pool-ului generat. Regenerați pool-ul.")
            if source is None and not (previous["rows"] and previous["date"]):
                raise ValueError("Istoricul inițial al pool-ului generat este indisponibil. Regenerați pool-ul.")

    if not inverse and number_range is not None:
        blocked = _range_exclusions(lot, scheme, number_range)
        forecast = _score_pool(lot, engine, scheme, dynamic, joker_size, excluded=blocked, hit_target=hit_target)
        if reuse and lot.bench_key_urna2:
            # A restriction on the main urn must preserve the generated Joker choice.
            forecast.update(jokers=previous["jokers"], joker_selection=previous["joker_selection"])
        forecast["eligible_count"] = lot.max_n - len(blocked)
    elif reuse:
        forecast = previous
        filename = result[0]
    else:
        forecast = _score_pool(lot, engine, scheme, dynamic, joker_size)

    pool = _balls(forecast["pool"], lot.max_n)
    jokers = _balls(forecast["jokers"], lot.geo.second.max_n) if lot.bench_key_urna2 else ()
    if lot.bench_key_urna2 and not jokers:
        raise ValueError("Pool-ul Urnei 2 Joker este indisponibil.")
    if inverse:
        forecast = _inverse_forecast(lot, engine, scheme, dynamic, pool, jokers, hit_target, number_range)
    else:
        forecast.update(pool=pool, jokers=jokers)
        forecast.setdefault("eligible_count", lot.max_n)
    forecast["number_range"] = number_range
    if size != scheme.n:
        forecast.update(expanded_from=size, expanded_to_max=required_pool_size(plan) > scheme.n)
    forecast.update(filename=filename, code=scheme.code, pool=sorted(forecast["pool"]), inverse=inverse)
    return forecast


def playable_plan_order(item):
    """Order playable tickets by exact probability, price and game identifier."""
    return (
        not item["tickets"], -Fraction(item["attained"], item["denominator"]),
        item["price_bani"], item["game"],
    )


def recommend_playable_games(
    budget_lei, objective, game_id=None, histories=None, *,
    pool_codes, generated=None, lookback=100, dynamic=True, joker_size=5, inverse=False, hit_target=None,
    inverse_range=None, number_range=None,
):
    """Rank the main urn within number_range. Legacy inverse_range applies only in inverse mode."""
    limits = number_range if number_range is not None or not inverse else inverse_range
    plans = recommend_games(budget_lei, objective, game_id)
    for plan in plans:
        lot = require_lottery(plan["game"], "RO")
        result = (generated or {}).get(lot.game_id)
        source = (histories or {}).get(lot.game_id)
        filename = result[0] if result else (source[0] if source else Path(lot.csv).name)
        try:
            forecast = _budget_forecast(
                plan, lot, pool_codes.get(lot.game_id), result, source,
                lookback, dynamic, joker_size, inverse, hit_target, limits,
            )
            if not restrict_to_pool(plan, objective, forecast["pool"], forecast["jokers"])["tickets"]:
                plan["forecast_error"] = f"Niciun cod LR în buget pentru pool-ul de {len(forecast['pool'])} numere."
            plan["forecast"] = forecast
        except (OSError, ValueError) as exc:
            logger.warning("Sugestie LR %s (%s): %s", lot.game_id, filename, exc)
            plan["forecast_error"] = f"{filename} · {exc}"
        except Exception:
            logger.exception("Sugestie LR pentru %s", lot.game_id)
            plan["forecast_error"] = f"{filename} · Calculul biletelor a eșuat."
        if "forecast" not in plan:
            plan.update(tickets=[], variants=0, price_bani=0, attained=0, upper=None, optimal=False)
    return sorted(plans, key=playable_plan_order)