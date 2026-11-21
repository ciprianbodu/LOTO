"""Fill a budget portfolio with balls ranked on the available draw history."""

from __future__ import annotations

import logging
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import numpy as np

from covering.lr_budget import recommend_games
from covering.lr_selection import select_lr_scheme
from loto_engine import LotoEngine
from loto_enterprise.core.dynamic_winner import choose_dynamic_winner
from loto_enterprise.core.history import history_dates, training_cutoffs
from loto_enterprise.core.lotteries import require_lottery
from loto_enterprise.core.ranking import rank_by_score


_ROOT = Path(__file__).resolve().parents[2]
logger = logging.getLogger(__name__)


@lru_cache(maxsize=32)
def _validated_rank(game, pool_size, draws, cutoffs):
    # Identical histories and pool sizes recur as the budget slider moves.
    lot = require_lottery(game, "RO")
    matrix = np.frombuffer(draws, dtype=np.int32).reshape(-1, lot.draw_n)
    scheme = select_lr_scheme(game, pool_size)
    scores, selection = choose_dynamic_winner(matrix, cutoffs, scheme)
    return rank_by_score(scores, lot.max_n), selection


def _bench_rank(engine, limit, *, bonus=False):
    scores = engine._get_timesfm_scores(is_joker_drum=bonus)
    maximum = engine.lottery.geo.second.max_n if bonus else engine.lottery.max_n
    ranking = rank_by_score({ball: score for ball, score in scores.items() if 1 <= ball <= maximum}, limit)
    if bonus and not ranking:
        ranking = engine._get_hard_core_joker(engine.analyze_joker_frequency(), pool_size=limit)
    key = engine._bench_game_key(bonus)
    selection = engine.audit.get("bench_winner", {}).get(key)
    if selection is None:
        selection = {"method": "frequency", "fallback": True}
    return ranking, selection


def _fill_portfolio(plan, engine, dynamic):
    zones = [zone for ticket in plan["tickets"] for zone in ticket]
    order = sorted(range(len(zones)), key=lambda i: -zones[i]["pool_size"])
    sizes = dict.fromkeys(zone["pool_size"] for zone in zones)
    maximum = engine.lottery.max_n
    rankings, selections = {}, {}
    if dynamic:
        draws = engine._draw_matrix.tobytes()
        cutoffs = training_cutoffs(engine.data)
    for size in sizes:
        if dynamic:
            ranking, selection = _validated_rank(plan["game"], size, draws, cutoffs)
        else:
            engine._winner_pool_hint = size
            ranking, selection = _bench_rank(engine, maximum)
        if len(ranking) != maximum:
            raise ValueError(f"Clasament incomplet pentru pool-ul de {size} numere.")
        rankings[size], selections[size] = ranking, deepcopy(selection)

    bonus_ranking, bonus_selection = [None] * len(zones), None
    if plan["game"] == "joker":
        if not engine._valid_joker_values().size:
            raise ValueError("Istoricul nu conține bile valabile pentru Urna 2 Joker.")
        bonus_ranking, bonus_selection = _bench_rank(engine, len(zones), bonus=True)
        if len(bonus_ranking) != len(zones):
            raise ValueError("Clasament incomplet pentru Urna 2 Joker.")

    # Preserve the optimizer's common anchor and disjoint private portions.
    # For 4+ this keeps the zone events mutually exclusive. For any-prize
    # portfolios the pools are disjoint, with distinct bonuses at Joker, so
    # relabeling these positions leaves the exact uniform coverage intact.
    primary_size = zones[order[0]]["pool_size"]
    anchor = rankings[primary_size][:plan["anchor_size"]]
    used = set(anchor)
    allocations = []
    for i, bonus in zip(order, bonus_ranking, strict=True):
        zone = zones[i]
        size = zone["pool_size"]
        private = [ball for ball in rankings[size] if ball not in used][:size - len(anchor)]
        if len(private) + len(anchor) != size:
            raise ValueError("Numere insuficiente pentru a păstra acoperirea portofoliului.")
        used.update(private)
        allocations.append((zone, sorted(anchor + private), bonus))
    for zone, numbers, bonus in allocations:
        zone.update(numbers=numbers, joker=bonus)
    return selections, bonus_selection


def recommend_playable_games(
    budget_lei, objective, game_id=None, histories=None, *, lookback=100, dynamic=True,
):
    """Apply the regular scoring to a portfolio without writing generation state.

    A loaded history takes precedence over the game's built-in CSV. Each
    history is a (filename, DataFrame) pair. Leave the cost and odds visible
    if a history is unusable, with empty zones in place of fixture numbers.
    """
    plans = recommend_games(budget_lei, objective, game_id)
    for plan in plans:
        lot = require_lottery(plan["game"], "RO")
        engine = LotoEngine(lot.geometry, game_key=lot.bench_key, country=lot.country)
        for ticket in plan["tickets"]:
            for zone in ticket:
                zone.update(numbers=[], joker=None)
        source = (histories or {}).get(lot.game_id)
        filename = source[0] if source is not None else Path(lot.csv).name
        try:
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
            dates = history_dates(engine.data)
            selections, bonus = _fill_portfolio(plan, engine, dynamic)
        except (OSError, ValueError) as exc:
            logger.warning("Sugestie LR %s (%s): %s", lot.game_id, filename, exc)
            plan["forecast_error"] = f"{filename} · {exc}"
        except Exception:
            logger.exception("Scoring sugestie LR pentru %s", lot.game_id)
            plan["forecast_error"] = f"{filename} · Calculul numerelor a eșuat."
        else:
            plan["forecast"] = {
                "filename": filename,
                "date": dates.iloc[-1].strftime("%d-%m-%Y") if dates is not None else None,
                "rows": len(engine.data),
                "selections": selections,
                "joker_selection": bonus,
                "dynamic": dynamic,
            }
    return plans
