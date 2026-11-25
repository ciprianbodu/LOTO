"""Causal comparison of predeclared main-urn ranges for the actual budget layout."""

from __future__ import annotations

import json
from collections import defaultdict
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from covering.lr_budget import ANY_PRIZE, recommend_games
from covering.lr_catalog_cache import catalog_cache
from covering.lr_pool_budget import restrict_to_pool
from covering.lr_pool_ranking import LR_MAX_CONSECUTIVE_RUN, lr_pool_capacity
from covering.lr_selection import resolve_lr_scheme
from loto_enterprise.benchmark.lr_provenance import lr_semantics
from loto_enterprise.benchmark.methods import call_method
from loto_enterprise.core.draw_validation import valid_draw_matrix
from loto_enterprise.core.dynamic_pools import ranked_pools
from loto_enterprise.core.dynamic_winner import (
    CANDIDATES, WINDOW_DAYS, WARMUP_DRAWS, _score_rows, _ticket_hit_counter, _winner,
)
from loto_enterprise.core.history import history_dates, training_cutoffs
from loto_enterprise.core.lotteries import require_lottery
from loto_enterprise.core.lr_budget_forecast import ONLINE_TICKET_EXPERTS, _forecast_engine, budget_plan_scheme
from loto_enterprise.core.lr_played_portfolio import expand_plan, grade_draw
from loto_enterprise.core.lr_range_timing import phase_evidence, range_timing
from loto_enterprise.core.score_validation import has_usable_score_variance


RANGES = (None, (10, 40))
VALIDATION_DRAWS = 100
RECENT_DRAWS = 25


def _left(origin, lookback):
    if 0 < lookback < 100:
        return origin - max(1, int(origin * (lookback / 100)))
    return 0


def _eligible(universe, limits):
    low, high = limits or (1, universe)
    return tuple(range(max(1, low), min(universe, high) + 1))


def _dynamic_predictor(matrix, bounds, days, scheme, universe, indicators, lookback):
    """Share raw prefix scores across treatments; re-select inside each eligible set."""
    # The production dynamic selector hashes int64 prefix bytes for its random control.
    matrix = np.ascontiguousarray(matrix, dtype=np.int64)
    reported = set()
    counter = _ticket_hit_counter(scheme)

    @lru_cache(maxsize=450)
    def bank(left, end):
        relative = np.maximum(0, bounds[left:end] - left)
        return _score_rows(CANDIDATES, matrix[left:end], relative, universe, reported)

    def prepare(origin):
        left = _left(origin, lookback)
        inner = days[(days >= left + WARMUP_DRAWS) & (days < origin)][-WINDOW_DAYS:]
        scores = np.stack([bank(left, int(end))[0] for end in (*inner, origin)])
        unavailable = bank(left, origin)[1]
        if len(inner):
            dates = bounds[inner[0]:origin]
            which_day = np.searchsorted(inner, dates)
            outcomes = indicators[inner[0]:origin]

        def pool(eligible):
            width, count = scheme.n, scores.shape[1]
            if lr_pool_capacity(eligible) < width:
                raise ValueError(f"Nu încap {width} numere fără trei consecutive în baza LR.")
            ranked = ranked_pools(
                scores.reshape(-1, universe), pool_size=width, universe=universe, eligible_numbers=eligible,
                max_run=LR_MAX_CONSECUTIVE_RUN,
            ).reshape(len(scores), count, width)
            totals = np.zeros((count, 1), dtype=int)
            if len(inner):
                columns = ranked[which_day].astype(np.intp) - 1
                matches = outcomes[np.arange(len(which_day))[:, None, None], columns]
                totals[:, 0] = (counter(matches) >= 4).sum(axis=0)
            winner, _ = _winner(totals, len(inner), (4,))
            if unavailable[winner]:
                winner = 0
            return ranked[-1, winner].astype(int)
        return pool
    return prepare


def _frozen_scores(method, past, maximum, cutoffs):
    if method != "frequency":
        try:
            scores, _ = call_method(method, past, maximum, history_cutoffs=cutoffs)
            if has_usable_score_variance(scores):
                return scores
        except (ValueError, ArithmeticError):
            pass
    return call_method("frequency", past, maximum, history_cutoffs=cutoffs)[0]


@catalog_cache(maxsize=8)
def _replay(data, date_labels, game, code, template, lookback, dynamic, inverse, joker_size,
            bonus_data, policy, semantic_signature):
    from loto_enterprise.benchmark.lr_budget_scoring import replay_budget_portfolios
    from loto_enterprise.core.lr_ticket_experts import online_ticket_forecast

    lot = require_lottery(game, "RO")
    scheme = resolve_lr_scheme(game, code)
    layout = json.loads(template)
    matrix = np.frombuffer(data, dtype=np.int32).reshape(-1, lot.draw_n)
    frame = pd.DataFrame(matrix, columns=[f"n{i + 1}" for i in range(lot.draw_n)])
    if date_labels is not None:
        frame["date"] = date_labels
    bonus = np.frombuffer(bonus_data, dtype=np.uint8)
    if lot.geo.second:
        frame["joker"] = bonus
    bounds = training_cutoffs(frame)
    days = [origin for origin in dict.fromkeys(bounds) if origin >= WARMUP_DRAWS]
    start = max(days[0], len(matrix) - VALIDATION_DRAWS) if days else len(matrix)
    origins = [day for day in days if day >= bounds[start]] if days else []
    results = np.full((len(RANGES), len(matrix) - start, 3), -1, dtype=np.int8)
    current = [False] * len(RANGES)
    if not len(matrix) or date_labels is None:
        return start, len(origins), results, current

    objective = layout["objective"]
    outcome = "any_prize" if objective == ANY_PRIZE else "hit4"
    for index, limits in enumerate(RANGES):
        base = _eligible(lot.max_n, limits)
        if lr_pool_capacity(base) < scheme.n or len(base) < scheme.n * (2 if inverse else 1):
            continue
        wanted = [*origins, len(matrix)]
        if policy != ONLINE_TICKET_EXPERTS:
            forecasts = replay_budget_portfolios(
                layout, lot, scheme, matrix, frame, wanted, inverse=inverse, dynamic=dynamic,
                lookback=lookback, joker_size=joker_size, number_range=limits,
            )
        for origin, end in zip(wanted, [*wanted[1:], len(matrix)], strict=True):
            if policy == ONLINE_TICKET_EXPERTS:
                try:
                    played = online_ticket_forecast(
                        lot, layout, scheme, frame.iloc[:origin], inverse=inverse, number_range=limits,
                        lookback=lookback, joker_size=joker_size, objective=objective,
                    )
                except ValueError:
                    continue
            else:
                forecast = forecasts[origin]
                if forecast.get("error"):
                    continue
                played = restrict_to_pool(deepcopy(layout), objective, forecast["pool"], forecast["jokers"])
                played["forecast_pool"] = forecast["pool"]
            if not played["tickets"]:
                continue
            if origin == len(matrix):
                current[index] = True
                continue
            portfolio = expand_plan(played)
            for i in range(max(start, origin), end):
                joker = (int(bonus[i]) or None) if lot.geo.second else None
                grade = grade_draw(portfolio, matrix[i], joker, draw_date=date_labels[i])
                attained = grade[outcome]
                results[index, i - start] = grade["pool_hits"], grade["max_hits"], -1 if attained is None else int(attained)
    return start, len(origins), results, current


def _summarize(data, reference, timing, start, limits, eligible_count, size):
    pool, tickets = data[:, 0], data[:, 1]
    played = pool >= 0
    base_played = reference[:, 0] >= 0
    evaluated = played & (data[:, 2] >= 0)
    base_evaluated = base_played & (reference[:, 2] >= 0)
    success = data[:, 2] == 1
    base_success = reference[:, 2] == 1
    compared = evaluated & base_evaluated
    differences = success.astype(int) - base_success.astype(int)
    paired = differences[compared]
    wins, losses = int((paired > 0).sum()), int((paired < 0).sum())

    # The layout has equal conditional win chances under uniform draws. Average
    # the differences within each forecast day so the evidence remains valid
    # when there are several dependent draws on that date. An unknown outcome
    # is bounded against the candidate to avoid selection on the observed bonus.
    lower_differences = success.astype(int) - (base_success | ~base_evaluated).astype(int)
    by_day = defaultdict(list)
    for (_, day), delta, both_played in zip(
        timing["_phase_keys"][start:], lower_differences, played & base_played, strict=True,
    ):
        if both_played:
            by_day[day].append(delta)
    daily_means = np.array([np.mean(values) for values in by_day.values()])
    evidence = sum(float(np.prod(1 + bet * daily_means)) for bet in (0.25, 0.5)) / 2
    adjusted = min(1.0, 1.0 / evidence)
    deltas = [None] * start + [int(delta) if ok else None for delta, ok in zip(differences, compared, strict=True)]
    phase = phase_evidence(timing, deltas)
    timing.pop("_phase_keys")
    half = len(pool) // 2
    stable = all(differences[sl][compared[sl]].sum() >= 0 for sl in (
        slice(None, half), slice(half, None), slice(-RECENT_DRAWS, None),
    ))
    return {
        "range": limits, "evaluated": int(played.sum()), "infeasible": int((~played).sum()),
        "pool_3plus": int((pool >= 3).sum()), "pool_4plus": int((pool >= 4).sum()),
        "ticket_3plus": int((tickets >= 3).sum()), "ticket_4plus": int((tickets >= 4).sum()),
        "successes": int(success.sum()), "objective_evaluated": int(evaluated.sum()),
        "objective_missing": int((played & ~evaluated).sum()), "recent_successes": int(success[-RECENT_DRAWS:].sum()),
        "recent_evaluated": int(evaluated[-RECENT_DRAWS:].sum()), "paired_evaluated": int(compared.sum()),
        "paired_wins": wins, "paired_losses": losses,
        "adjusted_p": adjusted, "eligible_now": eligible_count, "feasible_now": eligible_count >= size,
        "stable": bool(stable), "phase": phase, "chronology": timing,
    }


def _decision(rows):
    if not rows[0]["evaluated"] and not rows[0]["infeasible"]:
        return None, "Istoricul nu conține extrageri de validare după cele 250 de extrageri inițiale."
    if not rows[0]["evaluated"]:
        return None, "Selectorul nu a produs bilete jucabile pe baza completă în perioada de validare."
    if not rows[0]["objective_evaluated"]:
        return None, "Lipsesc rezultate valide din Urna 2 pentru a compara premiile pe perioada de test."
    restricted = rows[1]
    if not restricted["feasible_now"]:
        return None, "În intervalul 10–40, selectorul nu a produs bilete jucabile pentru schema din buget."
    if restricted["infeasible"]:
        return None, "Intervalul 10–40 nu a permis aceeași dimensiune a pool-ului la toate extragerile de validare."
    if restricted["adjusted_p"] > 0.05 or restricted["paired_wins"] <= restricted["paired_losses"]:
        return None, "Pe aceleași extrageri, avantajul intervalului 10–40 față de baza completă nu a atins pragul statistic de 5%."
    if not restricted["stable"]:
        return None, "Intervalul 10–40 a pierdut teren în una dintre perioadele de verificare."
    if not restricted["phase"]["supports"]:
        return None, "La pauze similare sunt prea puține observații sau rezultatele intervalului 10–40 sunt mai slabe."
    return restricted["range"], "Intervalul 10–40 a depășit baza completă pe variante și a trecut verificările de stabilitate și de pauză comparabilă."



def compare_budget_ranges(
    budget_lei, objective, game_id, histories=None, *, lookback=100, dynamic=True, inverse=False,
    hit_target=4, joker_size=5, pool_codes=None, generated=None, selection_policy=None,
):
    """Compare official main-number tickets before using a range for the next draw."""
    lot = require_lottery(game_id, "RO")
    if selection_policy not in (None, ONLINE_TICKET_EXPERTS):
        raise ValueError(f"Politică de selecție LR necunoscută: {selection_policy!r}.")
    plans = recommend_games(budget_lei, objective, game_id)
    if not plans or not plans[0]["tickets"]:
        raise ValueError("Bugetul nu produce bilete LR pentru comparația bazelor.")
    plan, = plans
    scheme = budget_plan_scheme(plan)
    source = (histories or {}).get(game_id)
    saved = (generated or {}).get(game_id)
    filename = source[0] if source else Path(lot.csv).name
    if saved and saved[0] != filename:
        raise ValueError("Lipsește istoricul original al pool-ului generat pentru comparația bazelor.")
    fingerprint = (saved[1].get("audit") or {}).get("hash") if saved else None
    engine = _forecast_engine(lot, source, 0, fingerprint)
    matrix = engine._draw_matrix
    dates = history_dates(engine.data)
    if lot.geo.second:
        maximum = lot.geo.second.max_n
        if type(joker_size) is not int or not 1 <= joker_size <= maximum:
            raise ValueError("Dimensiunea pool-ului Joker pentru Urna 2 este nevalidă.")
        if inverse and maximum - joker_size < joker_size:
            raise ValueError(f"Urna 2 Joker: rămân {maximum - joker_size} bile eligibile pentru un pool invers de {joker_size}.")
    bonus_prize = objective == ANY_PRIZE and game_id == "joker"
    bonus = np.zeros(len(matrix), dtype=np.uint8) if lot.geo.second else np.empty(0, dtype=np.uint8)
    if lot.geo.second and "joker" in engine.data:
        values, valid = valid_draw_matrix(engine.data, ["joker"], draw_n=1, max_num=lot.geo.second.max_n)
        bonus[valid] = values[:, 0]
    labels = tuple(dates.dt.strftime("%Y-%m-%d")) if dates is not None else None
    start, days, results, current = _replay(
        np.ascontiguousarray(matrix, dtype=np.int32).tobytes(), labels, game_id, scheme.code,
        json.dumps(plan, sort_keys=True), lookback, dynamic, inverse, joker_size, bonus.tobytes(), selection_policy,
        tuple(sorted(lr_semantics(lot, scheme, hit_target, layout=plan).items())),
    )
    rows = []
    for index, limits in enumerate(RANGES):
        timing = range_timing(matrix, dates, limits or (1, lot.max_n))
        base = _eligible(lot.max_n, limits)
        base_count = len(base)
        eligible_count = max(0, base_count - scheme.n) if inverse else base_count
        row = _summarize(
            results[index], results[0], timing, start, limits,
            eligible_count, scheme.n,
        )
        row["feasible_now"] = current[index]
        rows.append(row)
    recommendation, reason = _decision(rows)
    each_origin = "fiecărei zile" if dates is not None else "fiecărei extrageri"
    selector = "Fixed Share" if selection_policy else "dinamic" if dynamic else "fix"
    protocol = (
        f"Selectorul {selector} compară variantele plătite înaintea {each_origin}, "
        "folosind numai extragerile din zilele anterioare. Se verifică variantele finale și obiectivul ales."
    )
    if dates is None:
        recommendation, reason = None, "Istoricul nu are date calendaristice pentru validarea pe zile."
        protocol = "Se păstrează ordinea rândurilor din fișier pentru frecvențele intervalului. Validarea variantelor pe zile cere date calendaristice."
    if saved:
        protocol += " Comparația reconstruiește numai selecțiile de atunci. Pool-ul salvat pentru extragerea curentă nu se aplică retroactiv."
    protocol += " Pool-urile din urna principală au cel mult două numere consecutive."
    if inverse and dates is not None:
        protocol += (
            f" Înaintea {each_origin}, simularea calculează pool-ul direct separat pentru fiecare bază "
            "și îl exclude din acea bază înainte de a alege pool-ul invers."
        )
    metric = "4+ numere principale pe cel puțin o variantă" if objective != ANY_PRIZE else "Orice premiu pe o variantă"
    if bonus_prize:
        metric = "Orice premiu Joker pe o variantă"
        protocol += " Urna 2 se verifică pe fiecare variantă cu bila Joker atribuită zonei jucate."
    if game_id == "5/40":
        protocol += " La 5/40 se evaluează toate cele șase numere extrase."
    protocol += (
        " Validarea folosește ultimele 100 de extrageri eligibile. Avantajul se testează în perechi "
        "între 10–40 și baza completă, cu un prag de 5%, și se verifică în ambele jumătăți, "
        "în ultimele 25 de extrageri și la pauze similare."
    )
    if dates is not None:
        protocol += " Extragerile din aceeași zi contribuie cu un singur factor la testul statistic."
    if bonus_prize:
        protocol += " La Joker, rezultatele necunoscute se tratează conservator în acest test."
    return {
        "game": game_id, "code": scheme.code, "pool_size": scheme.n, "inverse": inverse,
        "rows": len(matrix) - start, "days": days, "first_date": (
            dates.iloc[start].date().isoformat() if dates is not None and len(matrix) > start else None
        ), "as_of": dates.iloc[-1].date().isoformat() if dates is not None and len(matrix) else None,
        "metric": metric, "ranges": rows,
        "recommended_range": recommendation, "reason": reason,
        "protocol": protocol,
    }
