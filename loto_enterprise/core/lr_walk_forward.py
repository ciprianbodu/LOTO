"""Holdout walk-forward for the selected Romanian LR budget hypothesis."""

from __future__ import annotations

from copy import deepcopy
from math import isfinite
from pathlib import Path

import numpy as np
import pandas as pd

from covering.lr_budget import ANY_PRIZE, recommend_games
from covering.lr_pool_budget import restrict_to_pool
from covering.lr_pool_ranking import LR_MAX_CONSECUTIVE_RUN, lr_pool_capacity, rank_lr_pool
from covering.lr_selection import resolve_lr_scheme
from loto_enterprise.benchmark import VALIDATION_PERCENTILES
from loto_enterprise.benchmark.decision import MIN_CONSISTENCY_WINDOWS, game_hit_target
from loto_enterprise.benchmark.lr_hypotheses import _cancellation, _training, benchmark_lr_hypotheses
from loto_enterprise.benchmark.runner import fold_sizes
from loto_enterprise.core.draw_validation import valid_draw_matrix
from loto_enterprise.core.history import history_dates, training_cutoffs
from loto_enterprise.core.lotteries import require_lottery
from loto_enterprise.core.lr_budget_forecast import _forecast_engine, _profile_scores, _same_profile_recipe, budget_plan_scheme, main_pool_shortfall
from loto_enterprise.core.lr_played_portfolio import expand_plan, grade_draw, layout_identity
from loto_enterprise.core.lr_range_analysis import compare_budget_ranges
from loto_enterprise.core.ranking import rank_by_score


_RANGE = (10, 40)
_SELECTION_ROLES = ("selected_method", "joker_selection", "direct_selection", "direct_joker_selection")


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


def _frequency_profile(target, reason):
    return {
        "members": [{"method": "frequency", "weight": 1.0}], "target": target,
        "fallback": True, "source": "training_frequency", "evaluated": 0, "folds": [], "reason": reason,
    }


def _fit(game, training, lot, *, inverse, cancel_path, **options):
    report = benchmark_lr_hypotheses(game, training, inverse=inverse, cancel_path=cancel_path, **options)
    fitted = {}
    for urn in report["urns"]:
        key = urn["game_key"]
        if fit := urn["decision"]:
            fitted[key] = {**fit, "source": "training_rebench"}
        else:
            target = game_hit_target(key, options["hit_target"]) if key == lot.bench_key else 1
            fitted[key] = _frequency_profile(target, urn.get("error", "Date insuficiente pentru alegerea metodei."))
    return fitted


def _rank(profile, matrix, bounds, origin, lookback, maximum, count, excluded, *, main_urn=False):
    past, cutoffs = _training(matrix, bounds, origin, lookback)
    scores, selection = _profile_scores((profile, "members"), past, cutoffs, maximum, excluded, count)
    ranking = rank_lr_pool(scores, count) if main_urn else rank_by_score(scores, count)
    return ranking, {"method": selection["method"], "fallback": selection["fallback"], "source": profile["source"]}


def _day_forecast(
    lot, matrix, bounds, origin, lookback, scheme, outside, inverse, methods,
    bonus_matrix, bonus_bounds, bonus_origin, joker_size,
):
    direct, tested = methods["direct"], methods["tested"]
    blocked = set(outside)
    excluded_pool = []
    selections = {}
    if inverse:
        normal, selections["direct_selection"] = _rank(
            direct[lot.bench_key], matrix, bounds, origin, lookback, lot.max_n, scheme.n, blocked, main_urn=True,
        )
        excluded_pool = sorted(normal)
        blocked.update(normal)
    pool, selections["selected_method"] = _rank(
        tested[lot.bench_key], matrix, bounds, origin, lookback, lot.max_n, scheme.n, blocked, main_urn=True,
    )

    jokers, excluded_jokers = [], []
    if key := lot.bench_key_urna2:
        if not bonus_origin:
            raise ValueError("Urna 2 Joker nu are extrageri valide înaintea acestei zile.")
        maximum = lot.geo.second.max_n
        if inverse:
            direct_bonus, selections["direct_joker_selection"] = _rank(
                direct[key], bonus_matrix, bonus_bounds, bonus_origin, lookback, maximum, joker_size, (),
            )
            excluded_jokers = sorted(direct_bonus)
        # Joker ordering is meaningful: the generator assigns each rank to a zone.
        jokers, selections["joker_selection"] = _rank(
            tested[key], bonus_matrix, bonus_bounds, bonus_origin, lookback, maximum, joker_size, excluded_jokers,
        )
    return {"pool": sorted(pool), "excluded_pool": excluded_pool, "eligible_count": lot.max_n - len(blocked),
            "jokers": jokers, "excluded_jokers": excluded_jokers, **selections}


def _summary(draws, days, start):
    scored = [draw for draw in draws if draw["pool_hits"] is not None]
    low, high = _RANGE
    fallbacks = [{role for role in _SELECTION_ROLES if (draw.get(role) or {}).get("fallback")} for draw in scored]
    return {
        "rows": len(draws), "days": days, "evaluated": len(scored), "infeasible": len(draws) - len(scored),
        "first_date": draws[0]["date"], "last_date": draws[-1]["date"], "training_rows": start,
        "all_main_in_10_40": sum(all(low <= n <= high for n in draw["numbers"]) for draw in draws),
        **{f"pool_{k}plus": sum(draw["pool_hits"] >= k for draw in scored) for k in range(3, 7)},
        **{f"ticket_{k}plus": sum(draw["ticket_hits"] >= k for draw in scored) for k in range(3, 7)},
        "paid_winning_variants": sum(d["winning_variants"] for d in scored)
        if all(d["winning_variants"] is not None for d in scored) else None,
        "paid_winning_variants_min": sum(d.get("winning_variants_min", d["winning_variants"]) for d in scored),
        "paid_winning_variants_max": sum(d.get("winning_variants_max", d["winning_variants"]) for d in scored),
        "any_prize_hits": sum(d["any_prize"] is True for d in scored),
        "any_prize_evaluated": sum(d["any_prize"] is not None for d in scored),
        "total_cost_bani": sum(d["price_bani"] for d in scored),
        "paid_variants": sum(d["paid_variants"] for d in scored),
        "distinct_variants": sum(d["distinct_variants"] for d in scored),
        "duplicate_variants": sum(d["duplicates"] for d in scored),
        "draws_with_duplicates": sum(bool(d.get("duplicates")) for d in scored),
        "fallback_draws": sum(bool(roles) for roles in fallbacks),
        "fallback_by_role": {role: sum(role in roles for roles in fallbacks) for role in _SELECTION_ROLES},
        "objective_hits": sum(draw["objective_hit"] is True for draw in scored),
        "objective_evaluated": sum(draw["objective_hit"] is not None for draw in scored),
        "joker_hits": sum(draw["joker_hit"] is True for draw in scored),
        "joker_evaluated": sum(draw["joker_hit"] is not None for draw in scored),
        "ticket_joker_hits": sum(draw["ticket_joker_hit"] is True for draw in scored),
        "ticket_joker_evaluated": sum(draw["ticket_joker_hit"] is not None for draw in scored),
    }


def validate_lr_walk_forward(
    game_id, history, *, budget_lei, objective, scheme, inverse, restricted, dynamic, lookback,
    hit_target, joker_size, depth, cancel_path=None, progress_cb=None, replay_policy="fixed", selection_policy=None,
):
    """Fit on the first chronological segment, then rerank on each prior-date prefix."""
    check = _cancellation(cancel_path)
    check(force=True)
    if not isfinite(depth := float(depth)) or not 0 < depth <= 100:
        raise ValueError("Adâncimea validării trebuie să fie în intervalul (0, 100].")
    if not isfinite(lookback := float(lookback)) or not 0 <= lookback <= 100:
        raise ValueError("Procentul de istoric pentru scoruri trebuie să fie între 0 și 100.")
    if selection_policy not in (None, "online_ticket_experts"):
        raise ValueError("Politică LR necunoscută.")
    if replay_policy not in ("fixed", "daily"):
        raise ValueError("Protocolul walk-forward trebuie să fie fixed sau daily.")
    daily = replay_policy == "daily" or selection_policy == "online_ticket_experts"
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
            **({"selection_policy": selection_policy} if selection_policy else {}),
        )
        restricted = comparison["recommended_range"] == _RANGE
        auto_reason = comparison["reason"]
    check(force=True)

    outside = set()
    if restricted:
        low, high = _RANGE
        outside = {n for n in range(1, lot.max_n + 1) if not low <= n <= high}
    eligible = set(range(1, lot.max_n + 1)).difference(outside)
    available = len(eligible) - (scheme.n if inverse else 0)
    if error := main_pool_shortfall(scheme, len(eligible), scheme.n if inverse else 0, _RANGE if restricted else None):
        raise ValueError(error)
    capacity = lr_pool_capacity(eligible)
    if capacity < scheme.n:
        raise ValueError(
            f"Urna principală: se pot alege cel mult {capacity} numere fără trei consecutive. "
            f"Cod {scheme.code} necesită {scheme.n}."
        )

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

    layout = restrict_to_pool(deepcopy(plan), objective, list(range(1, scheme.n + 1)), list(range(1, bonus_count + 1)))
    options = {
        "scheme": scheme.code, "restricted": bool(restricted), "dynamic": dynamic,
        "lookback": lookback, "hit_target": hit_target, "joker_size": joker_size,
        "budget_lei": budget_lei, "objective": objective,
    }
    methods = {}
    mismatched_references = []
    if not daily:
        direct = _fit(game_id, training, lot, inverse=False, cancel_path=cancel_path, **options)
        check(force=True)
        tested = _fit(game_id, training, lot, inverse=True, cancel_path=cancel_path, **options) if inverse else direct
        if inverse:
            mismatched_references = [
                key for key, profile in tested.items() if profile["source"] == "training_rebench"
                and not _same_profile_recipe(direct[key]["members"], profile.get("reference_members"))
            ]
            if mismatched_references:
                tested = {
                    key: _frequency_profile(profile["target"], "Referința directă diferă de cea evaluată la Re-Bench invers.")
                    for key, profile in tested.items()
                }
        methods = {"direct": direct, "tested": tested}

    days = tuple(dict.fromkeys(bounds[start:]))
    paid_by_scope = {}
    if daily and selection_policy is None:
        from loto_enterprise.benchmark.lr_budget_scoring import MIN_PORTFOLIO_DAYS, _MIN_TRAINING_ROWS, replay_budget_portfolios

        origins = [origin for origin in days if origin >= _MIN_TRAINING_ROWS + MIN_PORTFOLIO_DAYS]
        scopes = (False, True) if requested_restricted is None else (restricted,)
        paid_by_scope = {
            base: replay_budget_portfolios(
                plan, lot, scheme, matrix, frame, origins, inverse=inverse, dynamic=dynamic, lookback=lookback,
                joker_size=joker_size, number_range=_RANGE if base else None, check=check,
            ) for base in scopes
        }
    draws = []
    for origin, end in zip(days, (*days[1:], len(matrix)), strict=True):
        check()
        bonus_origin = int(np.searchsorted(valid_bonus_rows, origin))
        try:
            if daily:
                from loto_enterprise.core.lr_budget_forecast import recommend_playable_games
                prefix = {game_id: (training[0], frame.iloc[:origin])}
                daily_restricted = restricted
                if requested_restricted is None:
                    ranges = compare_budget_ranges(budget_lei, objective, game_id, prefix, dynamic=dynamic,
                                                   inverse=inverse, lookback=lookback, hit_target=hit_target,
                                                   joker_size=joker_size,
                                                   **({"selection_policy": selection_policy} if selection_policy else {}))
                    daily_restricted = ranges["recommended_range"] == _RANGE
                if selection_policy == "online_ticket_experts":
                    from loto_enterprise.core.lr_ticket_experts import online_ticket_forecast
                    daily_plan = online_ticket_forecast(
                        lot, plan, scheme, frame.iloc[:origin], inverse=inverse,
                        number_range=_RANGE if daily_restricted else None, lookback=lookback,
                        joker_size=joker_size, objective=objective, cancelled=check)
                else:
                    snapshot = paid_by_scope.get(daily_restricted, {}).get(origin)
                    options = {"paid_snapshot": {game_id: snapshot}} if snapshot is not None else {}
                    daily_plan, = recommend_playable_games(
                        budget_lei, objective, game_id, prefix, pool_codes={game_id: scheme.code},
                        lookback=lookback, dynamic=dynamic, joker_size=joker_size, inverse=inverse,
                        hit_target=hit_target, number_range=_RANGE if daily_restricted else None, **options)
                forecast = daily_plan.get("forecast")
                if not forecast or not daily_plan["tickets"]:
                    raise ValueError(daily_plan.get("forecast_error", "Bugetul nu produce bilete LR."))
                pool, jokers = list(forecast["pool"]), list(forecast["jokers"])
                played = {"pool": pool, "jokers": jokers, "eligible_count": forecast["eligible_count"],
                          "excluded_pool": list(forecast.get("excluded_pool", ())),
                          "excluded_jokers": list(forecast.get("excluded_jokers", ())),
                          "selected_method": forecast["selection"], "restricted": daily_restricted,
                          **{role: forecast[role] for role in _SELECTION_ROLES[1:] if forecast.get(role)}}
            else:
                played = _day_forecast(
                    lot, matrix, bounds, origin, lookback, scheme, outside, inverse, methods,
                    bonus_matrix, bonus_bounds, bonus_origin, joker_size,
                )
                pool, jokers = played["pool"], played["jokers"]
                daily_plan = restrict_to_pool(deepcopy(plan), objective, pool, jokers)
                daily_plan["forecast_pool"] = pool
            expanded = expand_plan(daily_plan)
            played["layout_id"] = layout_identity(daily_plan)
            played["tickets"] = daily_plan["tickets"]
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
                grade = grade_draw(expanded, numbers, joker, draw_date=day_labels[i])
                outcome = dict(grade, ticket_hits=grade["max_hits"],
                               objective_hit=grade["any_prize" if objective == ANY_PRIZE else "hit4"],
                               joker_hit=None, ticket_joker_hit=None)
                if joker is not None:
                    outcome["joker_hit"] = joker in jokers
                    outcome["ticket_joker_hit"] = any(
                        bonus == joker and set(variant).intersection(numbers)
                        for variant, bonus, _ in expanded.variants)
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
        "Pool-ul urnei principale păstrează cel mult două numere consecutive. "
        "Potrivirile pe bilet se măsoară pe cea mai bună variantă oficială."
    )
    if depth == 100:
        protocol += (
            " La 100% se validează toate zilele rămase după prefixul de antrenament. "
            "Prefixul păstrează ferestrele minime Re-Bench dacă istoricul permite."
        )
    if inverse:
        protocol += " În fiecare zi, pool-ul invers exclude pool-ul direct din aceeași bază."
    if mismatched_references:
        protocol += (
            " Re-Bench invers a evaluat altă referință directă. "
            "Validarea folosește frecvența pe complementul pool-ului direct fixat."
        )
    if dynamic:
        protocol += " Selecția dinamică se folosește la antrenament. Modelele alese rămân fixe în perioada de test."
    if lot.bench_key_urna2:
        protocol += " Intervalul se aplică numai urnei principale. La Joker, potrivirea bonusului pentru premiu se verifică pe aceeași variantă."
    if daily:
        protocol = ("Generatorul real a refăcut selecția metodei, a pool-ului și a biletelor pentru fiecare zi, "
                    "folosind exclusiv extragerile din zilele anterioare. Extragerile speciale au același prefix.")
    return {
        "game": game_id, "scheme_code": scheme.code, "history_hash": engine.audit["hash"], "history_rows": len(matrix),
        "as_of": day_labels[-1], "training_end": day_labels[start - 1], "depth": depth,
        "inverse": bool(inverse), "restricted": bool(restricted), "requested_restricted": requested_restricted,
        "max_consecutive_run": LR_MAX_CONSECUTIVE_RUN,
        "dynamic": bool(dynamic), "lookback": lookback, "hit_target": hit_target, "joker_size": joker_size,
        "selection_policy": selection_policy, "budget_lei": budget_lei, "objective": objective, "price_bani": layout["price_bani"], "variants": layout["variants"],
        "auto_reason": auto_reason, "summary": _summary(draws, len(days), start), "draws": draws,
        "methods": methods, "protocol": protocol, "replay_policy": "daily" if daily else "fixed",
        "inverse_references_checked": bool(inverse and not daily),
        "profile_reference_mismatch": mismatched_references,
    }
