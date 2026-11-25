"""Causal comparison of predeclared main-urn ranges for the actual budget layout."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import numpy as np

from covering.lr_budget import ANY_PRIZE, recommend_games
from covering.lr_pool_budget import restrict_to_pool
from covering.lr_pool_ranking import LR_MAX_CONSECUTIVE_RUN, lr_pool_capacity, rank_lr_pool
from covering.lr_schemes import scheme_tickets
from covering.lr_selection import resolve_lr_scheme
from covering.probability import subset_hit_table
from loto_enterprise.benchmark.methods import call_method
from loto_enterprise.benchmark.restricted_decision import restricted_bench_scores
from loto_enterprise.core.draw_validation import valid_draw_matrix
from loto_enterprise.core.dynamic_pools import ranked_pools
from loto_enterprise.core.dynamic_winner import (
    CANDIDATES, WINDOW_DAYS, WARMUP_DRAWS, _score_rows, _ticket_hit_counter, _winner,
)
from loto_enterprise.core.history import history_dates, training_cutoffs
from loto_enterprise.core.lotteries import require_lottery
from loto_enterprise.core.lr_budget_forecast import _forecast_engine, budget_plan_scheme, recommend_playable_games
from loto_enterprise.core.lr_range_timing import phase_evidence, range_timing
from loto_enterprise.core.ranking import rank_by_score
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


def _budget_layout(plan, objective, scheme, joker_size):
    pool = tuple(range(1, scheme.n + 1))
    jokers = tuple(range(1, joker_size + 1)) if plan["game"] == "joker" else ()
    layout = restrict_to_pool(deepcopy(plan), objective, pool, jokers)
    masks = set()
    touches = [0] * len(jokers)
    for ticket in layout["tickets"]:
        for zone in ticket:
            if jokers:
                touches[zone["joker"] - 1] |= sum(1 << (p - 1) for p in zone["numbers"])
            official = resolve_lr_scheme(plan["game"], zone["code"])
            for variant in scheme_tickets(official, zone["numbers"]):
                masks.add(sum(1 << (p - 1) for p in variant))
    if not masks:
        raise ValueError("Bugetul nu produce variante oficiale pentru comparație.")
    return tuple(sorted(masks)), tuple(touches)


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


def _fixed_predictor(matrix, bounds, first, lot, scheme, target, lookback):
    """Freeze a pre-validation Re-Bench winner, then update its scores on each prefix."""
    left = _left(first, lookback)
    _, selection = restricted_bench_scores(
        matrix[left:first], np.maximum(0, bounds[left:first] - left), lot.bench_key, scheme.n, (), target, strict_lr=True,
    )
    # The fixed production catalog currently selects one member. An old or
    # unavailable combined decision has the documented frequency fallback.
    method = selection["method"]
    if not method or " + " in method:
        method = "frequency"

    def prepare(origin):
        start = _left(origin, lookback)
        history = matrix[start:origin]
        cutoffs = tuple(np.maximum(0, bounds[start:origin] - start).tolist())
        scores = _frozen_scores(method, history, lot.max_n, cutoffs)

        def pool(eligible):
            return np.asarray(sorted(rank_lr_pool({n: scores[n] for n in eligible}, scheme.n)), dtype=int)
        return pool
    return prepare, method


def _bonus_predictor(values, bounds, first, lot, count, lookback, inverse):
    """Fit the bonus scorer before validation and rank only strictly past valid draws."""
    maximum = lot.geo.second.max_n

    def history(origin):
        start = _left(origin, lookback)
        valid = np.flatnonzero(values[start:origin]) + start
        groups = bounds[valid]
        return values[valid, None], tuple(np.searchsorted(groups, groups).tolist())

    initial, cutoffs = history(first)
    method = "frequency"
    if len(initial):
        _, decision = restricted_bench_scores(initial, cutoffs, lot.bench_key_urna2, 1, (), 1)
        method = decision["method"] or "frequency"
    if " + " in method:
        method = "frequency"

    def prepare(origin):
        past, cutoffs = history(origin)
        if not len(past):
            return None
        scores = _frozen_scores(method, past, maximum, cutoffs)
        ranked = rank_by_score(scores, maximum)
        return ranked[count:2 * count] if inverse else ranked[:count]
    return prepare, method


def _matched_bonus(hit_masks, drawn, selected, touches):
    # At least one main ball must be in a zone carrying the matching bonus.
    if selected is None:
        return np.full(len(drawn), -1, dtype=np.int8)
    zone_masks = np.zeros(21, dtype=np.int64)
    zone_masks[selected] = touches
    return np.where(drawn > 0, (hit_masks & zone_masks[drawn]) != 0, -1)


@lru_cache(maxsize=8)
def _replay(data, cutoffs, game, code, masks, lookback, dynamic, inverse, target, bonus_data, touches):
    lot = require_lottery(game, "RO")
    scheme = resolve_lr_scheme(game, code)
    matrix = np.frombuffer(data, dtype=np.int32).reshape(-1, lot.draw_n)
    bounds = np.asarray(cutoffs, dtype=np.intp)
    days = np.flatnonzero(bounds == np.arange(len(bounds)))
    eligible_days = days[days >= WARMUP_DRAWS]
    start = max(int(eligible_days[0]), len(matrix) - VALIDATION_DRAWS) if len(eligible_days) else len(matrix)
    # A partially scored date must still forecast before the first draw of its day.
    origins = eligible_days[eligible_days >= (bounds[start] if start < len(matrix) else start)]
    results = np.full((len(RANGES), len(matrix) - start, 3), -1, dtype=np.int8)
    if not len(origins):
        return start, 0, results, None, None
    indicators = np.zeros((len(matrix), lot.max_n), dtype=np.uint8)
    indicators[np.arange(len(matrix))[:, None], matrix - 1] = 1
    variant_hits = subset_hit_table(scheme.n, masks)[1]
    positions = 1 << np.arange(scheme.n)
    bases = tuple(_eligible(lot.max_n, limits) for limits in RANGES)
    required = 2 * scheme.n if inverse else scheme.n
    method = None
    if dynamic:
        prepare = _dynamic_predictor(matrix, bounds, days, scheme, lot.max_n, indicators, lookback)
    else:
        prepare, method = _fixed_predictor(matrix, bounds, int(origins[0]), lot, scheme, target, lookback)
    bonus_method = None
    if bonus_data:
        drawn_bonus = np.frombuffer(bonus_data, dtype=np.uint8)
        prepare_bonus, bonus_method = _bonus_predictor(
            drawn_bonus, bounds, int(origins[0]), lot, len(touches), lookback, inverse,
        )
    for origin, end in zip(origins, (*origins[1:], len(matrix)), strict=True):
        predict = prepare(int(origin))
        bonus = prepare_bonus(int(origin)) if bonus_data else None
        for i, base in enumerate(bases):
            if len(base) < required or lr_pool_capacity(base) < scheme.n:
                continue
            eligible = base
            if inverse:
                direct = set(predict(base))
                eligible = tuple(n for n in base if n not in direct)
                if lr_pool_capacity(eligible) < scheme.n:
                    continue
            pool = predict(eligible)
            scored_start = max(origin, start)
            matches = indicators[scored_start:end, pool - 1]
            dest = results[i, scored_start - start:end - start]
            dest[:, 0] = matches.sum(axis=1)
            hit_masks = matches @ positions
            dest[:, 1] = variant_hits[hit_masks]
            if bonus_data:
                dest[:, 2] = _matched_bonus(hit_masks, drawn_bonus[scored_start:end], bonus, touches)
    return start, len(origins), results, method, bonus_method


def _summarize(data, reference, threshold, timing, start, limits, eligible_count, size, bonus_prize=False):
    pool, tickets = data[:, 0], data[:, 1]
    played = pool >= 0
    base_played = reference[:, 0] >= 0
    base_tickets = reference[:, 1]
    evaluated, base_evaluated = played, base_played
    success = tickets >= threshold
    base_success = base_tickets >= threshold
    if bonus_prize:
        # A main-only prize and an entirely missed ticket are known without the bonus.
        evaluated = played & (success | (tickets == 0) | (data[:, 2] >= 0))
        base_evaluated = base_played & (base_success | (base_tickets == 0) | (reference[:, 2] >= 0))
        success |= data[:, 2] > 0
        base_success |= reference[:, 2] > 0
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
    if not rows[0]["evaluated"]:
        return None, "Istoricul nu conține extrageri de validare după cele 250 de extrageri inițiale."
    if not rows[0]["objective_evaluated"]:
        return None, "Lipsesc rezultate valide din Urna 2 pentru a compara premiile pe perioada de test."
    restricted = rows[1]
    if not restricted["feasible_now"]:
        return None, "În intervalul 10–40 nu rămân suficiente numere pentru schema din buget."
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
    hit_target=4, joker_size=5, pool_codes=None, generated=None,
):
    """Compare official main-number tickets before using a range for the next draw."""
    lot = require_lottery(game_id, "RO")
    plan, = recommend_games(budget_lei, objective, game_id)
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
    if inverse:
        current, = recommend_playable_games(
            budget_lei, objective, game_id, histories, pool_codes=pool_codes or {}, generated=generated,
            dynamic=dynamic, lookback=lookback, joker_size=joker_size, hit_target=hit_target,
        )
        if not current["tickets"]:
            raise ValueError(current.get("forecast_error", "Pool-ul normal nu poate fi determinat."))
        if lot.bench_key_urna2:
            bonus_size = len(current["forecast"]["jokers"])
            remaining = lot.geo.second.max_n - bonus_size
            if remaining < bonus_size:
                raise ValueError(
                    f"Urna 2 Joker: rămân {remaining} bile eligibile pentru un pool invers de {bonus_size}. "
                    "Comparația dintre baza completă și intervalul 10–40 nu poate produce bilete inverse."
                )
    masks, touches = _budget_layout(plan, objective, scheme, joker_size)
    bonus_prize = objective == ANY_PRIZE and game_id == "joker"
    bonus_data = b""
    if bonus_prize:
        bonus = np.zeros(len(matrix), dtype=np.uint8)
        if "joker" in engine.data:
            valid_values, valid = valid_draw_matrix(engine.data, ["joker"], draw_n=1, max_num=lot.geo.second.max_n)
            bonus[valid] = valid_values[:, 0]
        bonus_data = bonus.tobytes()
    start, days, results, method, bonus_method = _replay(
        np.ascontiguousarray(matrix, dtype=np.int32).tobytes(), training_cutoffs(engine.data),
        game_id, scheme.code, masks, lookback, dynamic, inverse, hit_target, bonus_data, touches,
    )
    threshold = 3 if objective == ANY_PRIZE and game_id != "5/40" else 4
    rows = []
    for index, limits in enumerate(RANGES):
        timing = range_timing(matrix, dates, limits or (1, lot.max_n))
        base = _eligible(lot.max_n, limits)
        base_count = len(base)
        eligible_count = max(0, base_count - scheme.n) if inverse else base_count
        row = _summarize(
            results[index], results[0], threshold, timing, start, limits,
            eligible_count, scheme.n, bonus_prize,
        )
        # Every third ball would have to be skipped even in one unbroken interval.
        # Only an intermediate-sized complement needs the exact current direct pool.
        if inverse and eligible_count - eligible_count // 3 < scheme.n <= eligible_count:
            reference = current
            if limits is not None:
                reference, = recommend_playable_games(
                    budget_lei, objective, game_id, histories, pool_codes=pool_codes or {}, generated=generated,
                    dynamic=dynamic, lookback=lookback, joker_size=joker_size, hit_target=hit_target, number_range=limits,
                )
            row["feasible_now"] = bool(reference["tickets"]) and (
                lr_pool_capacity(set(base).difference(reference["forecast"]["pool"])) >= scheme.n
            )
        elif not inverse:
            row["feasible_now"] = lr_pool_capacity(base) >= scheme.n
        rows.append(row)
    recommendation, reason = _decision(rows)
    origin = "zi" if dates is not None else "extragere"
    each_origin = "fiecărei zile" if dates is not None else "fiecărei extrageri"
    protocol = (
        f"Selectorul dinamic a fost recalculat înaintea {each_origin}, inclusiv pentru martorul aleator."
        if dynamic else f"Estimare în mod fix. Metoda {method or 'indisponibilă'} a fost aleasă prin Re-Bench "
        f"pe baza completă înaintea perioadei de test și folosită și pentru 10–40, cu scoruri actualizate la fiecare {origin}."
    )
    if dates is None:
        protocol += " Istoricul nu are date calendaristice. Se păstrează ordinea rândurilor din fișier."
    protocol += " Pool-urile din urna principală au cel mult două numere consecutive."
    if inverse:
        protocol += (
            f" Înaintea {each_origin}, simularea calculează pool-ul direct separat pentru fiecare bază "
            "și îl exclude din acea bază înainte de a alege pool-ul invers."
        )
    metric = f"{threshold}+ numere principale pe cel puțin o variantă"
    if bonus_prize:
        metric = "Orice premiu Joker pe o variantă"
        protocol += (
            f" Pentru Urna 2 se estimează bilele jucate cu metoda {bonus_method or 'indisponibilă'}, fixată "
            f"înaintea validării și actualizată pe extragerile anterioare {each_origin}. Ambele baze "
            "folosesc aceleași bile Joker pe aceleași zone. Sunt numărate trei numere principale sau "
            "cel puțin un număr principal cu bila Joker corectă pe aceeași variantă."
        )
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
        "metric": metric, "ranges": rows, "bonus_method": bonus_method,
        "recommended_range": recommendation, "reason": reason, "fixed_method": method,
        "protocol": protocol,
    }
