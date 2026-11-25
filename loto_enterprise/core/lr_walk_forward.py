"""Holdout walk-forward for the selected Romanian LR budget hypothesis."""

from __future__ import annotations

from copy import deepcopy
from math import isfinite
from pathlib import Path

import numpy as np
import pandas as pd

from covering.lr_budget import ANY_PRIZE, recommend_games
from covering.lr_pool_budget import restrict_to_pool
from covering.lr_schemes import scheme_tickets
from covering.lr_selection import resolve_lr_scheme
from covering.probability import subset_hit_table
from loto_enterprise.benchmark import VALIDATION_PERCENTILES
from loto_enterprise.benchmark.decision import MIN_CONSISTENCY_WINDOWS, game_hit_target
from loto_enterprise.benchmark.lr_hypotheses import _cancellation, _training, benchmark_lr_hypotheses
from loto_enterprise.benchmark.runner import fold_sizes
from loto_enterprise.core.draw_validation import valid_draw_matrix
from loto_enterprise.core.history import history_dates, training_cutoffs
from loto_enterprise.core.lotteries import require_lottery
from loto_enterprise.core.lr_budget_forecast import _forecast_engine, _profile_scores, budget_plan_scheme
from loto_enterprise.core.lr_range_analysis import compare_budget_ranges
from loto_enterprise.core.ranking import rank_by_score


_RANGE = (10, 40)


def _bench_windows(rows):
    minimum, _ = fold_sizes(rows, 100)
    return sum(
        start >= minimum and test > 0
        for start, test in (fold_sizes(rows, pct) for pct in VALIDATION_PERCENTILES)
    )


def _holdout(bounds, depth):
    size = len(bounds)
    first = size - max(1, int(size * depth / 100))
    if start := bounds[first]:
        return start
    origins = tuple(dict.fromkeys(origin for origin in bounds if origin))
    if not origins:
        raise ValueError("Validarea cere extrageri din cel puțin două zile distincte.")
    if depth == 100:
        # Test every remaining day after the shortest prefix that supports Re-Bench.
        for required in (MIN_CONSISTENCY_WINDOWS, 1):
            for origin in origins:
                if _bench_windows(origin) >= required:
                    return origin
    return origins[0]


def _fit(game, training, lot, *, inverse, cancel_path, **options):
    report = benchmark_lr_hypotheses(game, training, inverse=inverse, cancel_path=cancel_path, **options)
    fitted = {}
    for urn in report["urns"]:
        key = urn["game_key"]
        if fit := urn["decision"]:
            fitted[key] = {**fit, "source": "training_rebench"}
        else:
            fitted[key] = {
                "members": [{"method": "frequency", "weight": 1.0}],
                "target": game_hit_target(key, options["hit_target"]) if key == lot.bench_key else 1,
                "fallback": True, "source": "training_frequency", "evaluated": 0, "folds": [],
                "reason": urn.get("error", "Date insuficiente pentru alegerea metodei."),
            }
    return fitted


def _rank(profile, matrix, bounds, origin, lookback, maximum, count, excluded):
    past, cutoffs = _training(matrix, bounds, origin, lookback)
    scores, _ = _profile_scores((profile, "members"), past, cutoffs, maximum, excluded, count)
    return list(rank_by_score(scores, count))


def _ticket_layout(plan, objective, scheme, joker_count):
    positions = tuple(range(1, scheme.n + 1))
    jokers = tuple(range(1, joker_count + 1))
    layout = restrict_to_pool(deepcopy(plan), objective, positions, jokers)
    masks = set()
    bonus_touches = [0] * joker_count
    for ticket in layout["tickets"]:
        for zone in ticket:
            official = resolve_lr_scheme(plan["game"], zone["code"])
            for variant in scheme_tickets(official, zone["numbers"]):
                mask = sum(1 << (p - 1) for p in variant)
                masks.add(mask)
                if joker_count:
                    bonus_touches[zone["joker"] - 1] |= mask
    if not masks:
        raise ValueError("Bugetul nu produce bilete oficiale pe pool-ul selectat.")
    best_hits = subset_hit_table(scheme.n, tuple(sorted(masks)))[1]
    return layout, best_hits, bonus_touches


def _day_forecast(
    lot, matrix, bounds, origin, lookback, scheme, outside, inverse, methods,
    bonus_matrix, bonus_bounds, bonus_origin, joker_size,
):
    direct, tested = methods["direct"], methods["tested"]
    blocked = set(outside)
    excluded_pool = []
    if inverse:
        normal = _rank(direct[lot.bench_key], matrix, bounds, origin, lookback, lot.max_n, scheme.n, blocked)
        excluded_pool = sorted(normal)
        blocked.update(normal)
    pool = sorted(_rank(tested[lot.bench_key], matrix, bounds, origin, lookback, lot.max_n, scheme.n, blocked))

    jokers, excluded_jokers = [], []
    if key := lot.bench_key_urna2:
        if not bonus_origin:
            raise ValueError("Urna 2 Joker nu are extrageri valide înaintea acestei zile.")
        maximum = lot.geo.second.max_n
        if inverse:
            excluded_jokers = sorted(_rank(
                direct[key], bonus_matrix, bonus_bounds, bonus_origin, lookback, maximum, joker_size, (),
            ))
        # Joker ordering is meaningful: the generator assigns each rank to a zone.
        jokers = _rank(
            tested[key], bonus_matrix, bonus_bounds, bonus_origin, lookback, maximum, joker_size, excluded_jokers,
        )
    return pool, excluded_pool, len(blocked), jokers, excluded_jokers


def _outcome(numbers, pool, joker, jokers, best_hits, bonus_touches, game, objective):
    actual = set(numbers)
    mask = sum(1 << index for index, number in enumerate(pool) if number in actual)
    ticket_hits = int(best_hits[mask])
    joker_hit = ticket_joker_hit = None
    if joker is not None:
        joker_hit = joker in jokers
        ticket_joker_hit = joker_hit and bool(mask & bonus_touches[jokers.index(joker)])
    threshold = 3 if objective == ANY_PRIZE and game != "5/40" else 4
    objective_hit = ticket_hits >= threshold
    if not objective_hit and game == "joker" and objective == ANY_PRIZE and ticket_hits > 0:
        objective_hit = ticket_joker_hit
    return {
        "pool_hits": mask.bit_count(), "ticket_hits": ticket_hits,
        "joker_hit": joker_hit, "ticket_joker_hit": ticket_joker_hit, "objective_hit": objective_hit,
    }


def _summary(draws, days, start):
    scored = [draw for draw in draws if draw["pool_hits"] is not None]
    low, high = _RANGE
    return {
        "rows": len(draws), "days": days, "evaluated": len(scored), "infeasible": len(draws) - len(scored),
        "first_date": draws[0]["date"], "last_date": draws[-1]["date"], "training_rows": start,
        "all_main_in_10_40": sum(all(low <= n <= high for n in draw["numbers"]) for draw in draws),
        **{f"pool_{k}plus": sum(draw["pool_hits"] >= k for draw in scored) for k in range(3, 7)},
        **{f"ticket_{k}plus": sum(draw["ticket_hits"] >= k for draw in scored) for k in range(3, 7)},
        "objective_hits": sum(draw["objective_hit"] is True for draw in scored),
        "objective_evaluated": sum(draw["objective_hit"] is not None for draw in scored),
        "joker_hits": sum(draw["joker_hit"] is True for draw in scored),
        "joker_evaluated": sum(draw["joker_hit"] is not None for draw in scored),
        "ticket_joker_hits": sum(draw["ticket_joker_hit"] is True for draw in scored),
        "ticket_joker_evaluated": sum(draw["ticket_joker_hit"] is not None for draw in scored),
    }


def validate_lr_walk_forward(
    game_id, history, *, budget_lei, objective, scheme, inverse, restricted, dynamic, lookback,
    hit_target, joker_size, depth, cancel_path=None, progress_cb=None,
):
    """Fit on the first chronological segment, then rerank on each prior-date prefix."""
    check = _cancellation(cancel_path)
    check(force=True)
    if not isfinite(depth := float(depth)) or not 0 < depth <= 100:
        raise ValueError("Adâncimea validării trebuie să fie în intervalul (0, 100].")
    if not isfinite(lookback := float(lookback)) or not 0 <= lookback <= 100:
        raise ValueError("Procentul de istoric pentru scoruri trebuie să fie între 0 și 100.")
    lot = require_lottery(game_id, "RO")
    plan, = recommend_games(budget_lei, objective, game_id)
    scheme = resolve_lr_scheme(lot.geometry, getattr(scheme, "code", scheme))
    if budget_plan_scheme(plan).code != scheme.code:
        raise ValueError("Codul LR nu corespunde planului pentru buget și obiectiv.")

    source = (Path(lot.csv).name, history) if isinstance(history, pd.DataFrame) else history
    engine = _forecast_engine(lot, source, 0)
    matrix, frame = engine._draw_matrix, engine.data
    dates = history_dates(frame)
    if dates is None:
        raise ValueError("Validarea cronologică cere data fiecărei extrageri.")
    bounds = training_cutoffs(frame)
    day_labels = [value.date().isoformat() for value in dates]
    start = _holdout(bounds, depth)
    total = len(matrix) - start
    if progress_cb is not None:
        progress_cb(0, total)
    training = (source[0] if source is not None else Path(lot.csv).name, frame.iloc[:start])
    requested_restricted = restricted
    auto_reason = None
    if restricted is None:
        comparison = compare_budget_ranges(
            budget_lei, objective, game_id, {game_id: training}, dynamic=dynamic, inverse=inverse,
            lookback=lookback, hit_target=hit_target, joker_size=joker_size,
        )
        restricted = comparison["recommended_range"] == _RANGE
        auto_reason = comparison["reason"]
    check(force=True)

    outside = set()
    if restricted:
        low, high = _RANGE
        outside = {n for n in range(1, lot.max_n + 1) if not low <= n <= high}
    available = lot.max_n - len(outside) - (scheme.n if inverse else 0)
    if available < scheme.n:
        raise ValueError(f"Urna principală: rămân {max(0, available)} numere eligibile. Cod {scheme.code} necesită {scheme.n}.")

    bonus_matrix, bonus_bounds = None, ()
    valid_bonus_rows = np.empty(0, dtype=np.intp)
    aligned_bonus = [None] * len(matrix)
    bonus_count = 0
    if lot.bench_key_urna2:
        maximum = lot.geo.second.max_n
        if type(joker_size) is not int or not 1 <= joker_size <= maximum:
            raise ValueError("Dimensiunea pool-ului Joker pentru Urna 2 este nevalidă.")
        bonus_count = joker_size
        bonus_available = maximum - (joker_size if inverse else 0)
        if bonus_available < joker_size:
            raise ValueError(f"Urna 2 Joker: rămân {bonus_available} bile eligibile. Sunt necesare {joker_size}.")
        if "joker" in frame:
            bonus_matrix, valid = valid_draw_matrix(frame, ["joker"], draw_n=1, max_num=maximum)
            bonus_bounds = training_cutoffs(frame.loc[valid])
            valid_bonus_rows = np.flatnonzero(valid)
            for index, number in zip(valid_bonus_rows, bonus_matrix[:, 0], strict=True):
                aligned_bonus[index] = int(number)
        if not len(valid_bonus_rows) or valid_bonus_rows[0] >= bounds[-1]:
            raise ValueError("Urna 2 Joker nu are extrageri valide înaintea niciunei zile de test.")

    layout, best_hits, bonus_touches = _ticket_layout(plan, objective, scheme, bonus_count)
    options = {
        "scheme": scheme.code, "restricted": bool(restricted), "dynamic": dynamic,
        "lookback": lookback, "hit_target": hit_target, "joker_size": joker_size,
    }
    direct = _fit(game_id, training, lot, inverse=False, cancel_path=cancel_path, **options)
    check(force=True)
    tested = _fit(game_id, training, lot, inverse=True, cancel_path=cancel_path, **options) if inverse else direct
    methods = {"direct": direct, "tested": tested}

    days = tuple(dict.fromkeys(bounds[start:]))
    draws = []
    for origin, end in zip(days, (*days[1:], len(matrix)), strict=True):
        check()
        bonus_origin = int(np.searchsorted(valid_bonus_rows, origin))
        try:
            pool, excluded, blocked_count, jokers, excluded_jokers = _day_forecast(
                lot, matrix, bounds, origin, lookback, scheme, outside, inverse, methods,
                bonus_matrix, bonus_bounds, bonus_origin, joker_size,
            )
            played = {
                "pool": pool, "excluded_pool": excluded, "eligible_count": lot.max_n - blocked_count,
                "jokers": jokers, "excluded_jokers": excluded_jokers,
            }
        except (ValueError, ArithmeticError) as exc:
            played = {
                "pool": [], "excluded_pool": [], "eligible_count": available, "jokers": [], "excluded_jokers": [],
                "error": str(exc),
            }
        for i in range(origin, end):
            numbers = [int(n) for n in matrix[i]]
            joker = aligned_bonus[i]
            if "error" in played:
                outcome = dict.fromkeys(("pool_hits", "ticket_hits", "joker_hit", "ticket_joker_hit", "objective_hit"))
            else:
                outcome = _outcome(numbers, pool, joker, jokers, best_hits, bonus_touches, game_id, objective)
            draws.append({
                "date": day_labels[i], "numbers": numbers, "joker_drawn": joker, "training_rows": origin,
                **played, **outcome,
            })
            if progress_cb is not None:
                progress_cb(len(draws), total)
    check(force=True)

    protocol = (
        "Metodele se aleg o singură dată, din antrenamentul anterior perioadei de test. "
        "Când Re-Bench nu poate alege o metodă pe acel istoric, se folosește frequency. "
        "Pentru fiecare zi, pool-urile se refac cu scoruri bazate numai pe extragerile din zilele anterioare. "
        "Toate extragerile din test contează, inclusiv cele cu numere în afara intervalului. "
        "Potrivirile pe bilet se măsoară pe cea mai bună variantă oficială."
    )
    if depth == 100:
        protocol += (
            " La 100% se validează toate zilele rămase după prefixul de antrenament. "
            "Prefixul păstrează ferestrele minime Re-Bench dacă istoricul permite."
        )
    if inverse:
        protocol += " În fiecare zi, pool-ul invers exclude pool-ul direct din aceeași bază."
    if dynamic:
        protocol += " Selecția dinamică se folosește la antrenament. Modelele alese rămân fixe în perioada de test."
    if lot.bench_key_urna2:
        protocol += " Intervalul se aplică numai urnei principale. La Joker, potrivirea bonusului pentru premiu se verifică pe aceeași variantă."
    return {
        "game": game_id, "scheme_code": scheme.code, "history_hash": engine.audit["hash"], "history_rows": len(matrix),
        "as_of": day_labels[-1], "training_end": day_labels[start - 1], "depth": depth,
        "inverse": bool(inverse), "restricted": bool(restricted), "requested_restricted": requested_restricted,
        "dynamic": bool(dynamic), "lookback": lookback, "hit_target": hit_target, "joker_size": joker_size,
        "budget_lei": budget_lei, "objective": objective, "price_bani": layout["price_bani"], "variants": layout["variants"],
        "auto_reason": auto_reason, "summary": _summary(draws, len(days), start), "draws": draws,
        "methods": methods, "protocol": protocol,
    }
