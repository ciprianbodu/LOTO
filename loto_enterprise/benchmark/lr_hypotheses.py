"""Walk-forward Re-Bench for a specific LR selection hypothesis."""

from __future__ import annotations

from concurrent.futures import CancelledError, FIRST_COMPLETED, ProcessPoolExecutor, wait
from functools import partial
from math import isfinite
from pathlib import Path
from time import monotonic

import numpy as np
import pandas as pd

from covering.lr_selection import resolve_lr_scheme
from covering.lr_pool_ranking import LR_MAX_CONSECUTIVE_RUN, LR_RESTRICTED_RANGE, lr_pool_capacity, rank_lr_pool
from loto_enterprise.benchmark import VALIDATION_PERCENTILES, decision
from loto_enterprise.benchmark.lr_provenance import lr_semantics
from loto_enterprise.benchmark.curated import apply_curation, resolve_methods_per_game
from loto_enterprise.benchmark.methods import call_method, list_methods, method_meta
from loto_enterprise.benchmark.restricted_decision import _lr_fold_pool, restricted_bench_scores
from loto_enterprise.benchmark.runner import (
    GameDef, _evaluate_fold, _methods_for_game, bench_worker_count, fold_sizes,
)
from loto_enterprise.core.draw_validation import valid_draw_matrix
from loto_enterprise.core.history import history_dates, training_cutoffs
from loto_enterprise.core.lotteries import require_lottery
from loto_enterprise.core.lr_budget_forecast import _forecast_engine
from loto_enterprise.core.lr_range_analysis import _dynamic_predictor, _frozen_scores, _left
from loto_enterprise.core.ranking import rank_by_score


def _cancellation(cancel_path):
    deadline = 0.0
    cancelled = False
    marker = Path(cancel_path) if cancel_path is not None else None

    def check(*, force=False):
        nonlocal deadline, cancelled
        if marker is None:
            return
        now = monotonic()
        if not cancelled and (force or now >= deadline):
            cancelled = marker.exists()
            deadline = now + 0.2
        if cancelled:
            raise CancelledError("Re-Bench LR a fost anulat.")

    return check


def _methods(game_key):
    available = [name for name in list_methods() if method_meta(name).get("available", True)]
    methods, curation = apply_curation(available)
    per_game = resolve_methods_per_game(methods, [game_key]) if curation["active"] else None
    selected = _methods_for_game(methods, per_game, game_key)
    return tuple(dict.fromkeys((*selected, "frequency", "random")))


def _training(matrix, bounds, origin, lookback):
    left = _left(origin, lookback)
    window = matrix[left:origin]
    cutoffs = tuple(max(0, day - left) for day in bounds[left:origin])
    return window, cutoffs


def _blocked_by_date(
    matrix, bounds, game, scheme, origins, base, reference_size, dynamic, lookback, target, check, *, calendar,
):
    """Compute the direct reference once at each date, before scoring candidates."""
    check(force=True)
    outside_base = frozenset(range(1, game.max_num + 1)).difference(base)
    if not reference_size:
        return dict.fromkeys(origins, outside_base), "", []

    reference_members = []
    each_origin = "fiecărei zile" if calendar else "fiecărei extrageri"
    if dynamic and scheme is not None:
        limits = np.asarray(bounds, dtype=np.intp)
        days = np.flatnonzero(limits == np.arange(len(limits)))
        indicators = np.zeros((len(matrix), game.max_num), dtype=np.uint8)
        indicators[np.arange(len(matrix))[:, None], matrix - 1] = 1
        prepare = _dynamic_predictor(
            matrix, limits, days, scheme, game.max_num, indicators, lookback,
        )
        def direct_pool(origin):
            return prepare(origin)(base)
        protocol = f"Pool-ul direct este ales de selectorul dinamic, separat înaintea {each_origin}."
    else:
        initial, cutoffs = _training(matrix, bounds, origins[0], lookback)
        _, selection = restricted_bench_scores(
            initial, cutoffs, game.key, game.base_k if game.is_single_pick else reference_size,
            outside_base, target, strict_lr=not game.is_single_pick,
        )
        method = selection["method"]
        if not method or " + " in method:
            method = "frequency"
        reference_members = [{"method": method, "weight": 1.0}]

        def direct_pool(origin):
            past, cuts = _training(matrix, bounds, origin, lookback)
            scores = _frozen_scores(method, past, game.max_num, cuts)
            eligible = {n: scores[n] for n in base if n in scores}
            return (rank_by_score if game.is_single_pick else rank_lr_pool)(eligible, reference_size)

        protocol = (
            f"Pool-ul direct folosește metoda {method}, fixată pe datele disponibile "
            f"înaintea primei {'zile' if calendar else 'extrageri'} evaluate, "
            f"cu scoruri actualizate înaintea {each_origin}."
        )

    blocked = {}
    for origin in origins:
        check()
        direct = frozenset(direct_pool(origin))
        if len(direct) != reference_size or direct.difference(base):
            raise ValueError("Pool-ul direct al unei date nu poate fi completat în baza aleasă.")
        blocked[origin] = outside_base | direct
    return blocked, protocol, reference_members


def _evaluate_method(method, *, matrix, game, windows, blocked, lookback, size, cancel_path):
    """Reuse each prefix score across the overlapping Re-Bench windows."""
    scores_by_origin = {}
    check = _cancellation(cancel_path)

    def score_prefix(name, history, maximum, **_context):
        check()
        origin = len(history)
        if blocked[origin] is None:
            # The date remains in history for later fits, but no candidate had a playable pool on it.
            return {}, 0.0
        if origin not in scores_by_origin:
            past, cuts = _training(matrix, game.history_cutoffs, origin, lookback)
            scores, duration = call_method(name, past, maximum, history_cutoffs=cuts)
            filtered = {n: score for n, score in scores.items() if n not in blocked[origin]}
            scores_by_origin[origin] = (filtered if len(filtered) >= size else {}, duration)
        return scores_by_origin[origin]

    records = []
    for pct, start in windows:
        check(force=True)
        fold, _ = _evaluate_fold(
            method, matrix[:start], matrix[start:], game, 1, score_method=score_prefix,
            rank_pool=None if game.is_single_pick else _lr_fold_pool, selection_size=size,
        )
        check(force=True)
        fold.percentile = pct
        if any(excluded is None for excluded in blocked.values()):
            # Decision completeness is relative to the common playable sample.
            # n_eval can still be lower if this particular scorer failed on a playable date.
            fold.n_test = sum(blocked[day] is not None for day in game.history_cutoffs[start:])
        records.append(fold.as_record())
    return records


def _evaluate_parallel(evaluate, methods, workers, check):
    results = {}
    with ProcessPoolExecutor(max_workers=workers) as pool:
        jobs = {}
        try:
            for method in methods:
                check(force=True)
                jobs[pool.submit(evaluate, method)] = method
            pending = set(jobs)
            while pending:
                check(force=True)
                done, pending = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
                for job in done:
                    results[jobs[job]] = job.result()
        except CancelledError:
            for job in jobs:
                job.cancel()
            raise
    return [record for method in methods for record in results[method]]


def _reusable_decision(config, records, target, history_rows, reference_members):
    """Keep only benchmarked ensemble members and their common positive samples."""
    if config.get("error"):
        return None
    samples = {}
    for row in records:
        if not row["failed"] and row["n_eval"] > 0:
            samples.setdefault(row["method"], {})[int(row["percentile"])] = int(row["n_eval"])

    selected = config.get("ensemble") or [{"method": config.get("scorer"), "weight": 1.0}]
    weighted = []
    for member in selected:
        method, weight = member["method"], float(member["weight"])
        if method in samples and method != "random" and isfinite(weight) and weight > 0:
            weighted.append((method, weight))
    if not weighted:
        return None

    common = set.intersection(*(set(samples[method]) for method, _ in weighted))
    if not common:
        return None
    widest = max(common)
    total = sum(weight for _, weight in weighted)
    return {
        "members": [{"method": method, "weight": weight / total} for method, weight in weighted],
        "target": target,
        "fallback": bool(config.get("fallback") or config.get("low_confidence") or len(weighted) != len(selected)),
        "evaluated": min(samples[method][widest] for method, _ in weighted),
        "folds": sorted(common),
        "history_rows": history_rows,
        "reference_members": reference_members,
    }


def _bench_urn(
    name, matrix, game, size, base, reference_size, required, scheme, dynamic, lookback, target, cancel_path, *, calendar,
):
    check = _cancellation(cancel_path)
    check(force=True)
    eligible_count = max(0, len(base) - reference_size)
    output = {
        "name": name, "game_key": game.key, "pool_size": size, "target": target, "eligible_count": eligible_count,
        "rows": [], "method": None, "fallback": False, "folds": [], "protocol": "", "decision": None,
    }
    if eligible_count < required:
        output["error"] = f"Rămân {eligible_count} numere eligibile. Sunt necesare {required}."
        return output
    if not game.is_single_pick and lr_pool_capacity(base) < required:
        output["error"] = f"În baza aleasă nu încap {required} numere fără trei consecutive."
        return output

    windows = []
    for pct in VALIDATION_PERCENTILES:
        start, test = fold_sizes(len(matrix), pct)
        if start >= 80 and test > 0:
            windows.append((pct, start))
    if not windows:
        output["error"] = "Istoricul este insuficient. Sunt necesare cel puțin 81 de extrageri valide."
        return output

    earliest = min(start for _, start in windows)
    origins = tuple(sorted(set(game.history_cutoffs[earliest:])))
    if origins[0] == 0:
        output["error"] = "Nu există extrageri din zile anterioare pentru prima zi de test."
        return output
    blocked, reference_protocol, reference_members = _blocked_by_date(
        matrix, game.history_cutoffs, game, scheme, origins, base, reference_size,
        dynamic, lookback, target, check, calendar=calendar,
    )
    coverage = ""
    if not game.is_single_pick and reference_size:
        base_set = set(base)
        skipped = {origin for origin, excluded in blocked.items() if lr_pool_capacity(base_set - excluded) < size}
        if skipped:
            cutoffs = game.history_cutoffs[earliest:]
            missing = sum(day in skipped for day in cutoffs)
            output.update(infeasible_days=len(skipped) if calendar else None, infeasible_draws=missing,
                          playable_draws=len(cutoffs) - missing, test_draws=len(cutoffs))
            if len(skipped) == len(origins):
                output["error"] = (
                    f"În toate cele {missing} extrageri de test, după excluderea pool-ului direct, "
                    f"nu încap {size} numere fără trei consecutive."
                )
                return output
            for origin in skipped:
                blocked[origin] = None
            windows = [(pct, start) for pct, start in windows
                       if any(day not in skipped for day in game.history_cutoffs[start:])]
            omitted = f"În {len(skipped)} zile ({missing} extrageri)" if calendar else f"La {missing} extrageri"
            coverage = (
                f" În cea mai lungă fereastră au fost disponibile bilete la {len(cutoffs) - missing} din "
                f"{len(cutoffs)} extrageri. {omitted}, pool-ul invers nu "
                "a încăput fără trei numere consecutive. Aceleași extrageri au fost omise pentru toate metodele."
            )
    methods = _methods(game.key)
    evaluate = partial(
        _evaluate_method, matrix=matrix, game=game, windows=windows, blocked=blocked,
        lookback=lookback, size=size, cancel_path=cancel_path,
    )
    workers = min(max(1, bench_worker_count()), len(methods))
    if workers > 1 and len(origins) >= 100 and len(methods) >= 4:
        records = _evaluate_parallel(evaluate, methods, workers, check)
    else:
        records = [record for method in methods for record in evaluate(method)]
    check(force=True)

    folds = pd.DataFrame(records)
    config = decision.decide_optimal_config_for_pool(
        folds, game.key, size, game.draw_n, game.max_num, game.pick_n,
        hit_target=target, candidate_methods=methods,
    )
    rate_col = f"rate_{target}plus_k{size}"
    widest_pct = max(pct for pct, start in windows if start == earliest)
    widest = [
        {
            "method": row["method"], "evaluated": int(row["n_eval"]),
            "hit_rate": float(row[rate_col]), "avg_hits": float(row[f"k{size}"]),
        }
        for row in records if row["percentile"] == widest_pct and not row["failed"]
    ]
    widest.sort(key=lambda row: (-row["hit_rate"], -row["avg_hits"], row["method"]))
    reusable = _reusable_decision(config, records, target, len(matrix), reference_members)
    method = " + ".join(member["method"] for member in reusable["members"]) if reusable else None
    chronology = (
        "Metodele folosesc numai extragerile disponibile înaintea fiecărei zile. "
        if calendar else "Istoricul nu are date calendaristice. Metodele folosesc numai rândurile anterioare, în ordinea fișierului. "
    )
    output.update(
        rows=widest, method=method, fallback=bool(reusable and reusable["fallback"]), decision=reusable,
        folds=[pct for pct, _ in windows],
        protocol=(
            "Clasamentul arată fereastra cea mai lungă. Decizia folosește toate ferestrele disponibile. "
            + chronology + "Extragerile cu numere în afara bazei sunt incluse. "
            + reference_protocol + coverage
        ).rstrip(),
    )
    if not reusable:
        output["error"] = config.get("error") or "Decizia nu conține metode cu rezultate utilizabile pentru recomandare."
    return output


def benchmark_lr_hypotheses(
    game_id, history, *, scheme, inverse, restricted, dynamic, lookback, hit_target, joker_size,
    budget_lei=None, objective=None, cancel_path=None, recipe=None,
):
    """Evaluate curated Re-Bench methods for these choices without writing a production decision."""
    check = _cancellation(cancel_path)
    check(force=True)
    lot = require_lottery(game_id, "RO")
    from covering import lr_schemes
    from covering.lr_catalog_cache import refresh_catalog
    refresh_catalog(lr_schemes.LR_DIR)
    scheme = resolve_lr_scheme(lot.geometry, getattr(scheme, "code", scheme))
    layout = None
    if budget_lei is not None or objective is not None:
        from covering.lr_budget import recommend_games
        layout, = recommend_games(budget_lei, objective, game_id)
    semantics = lr_semantics(lot, scheme, hit_target, layout=layout)
    source = (Path(lot.csv).name, history) if isinstance(history, pd.DataFrame) else history
    engine = _forecast_engine(lot, source, 0)
    matrix = engine._draw_matrix
    dates = history_dates(engine.data)
    main = GameDef(
        lot.bench_key, lot.name, "", [], lot.max_n, lot.draw_n,
        pool_extra=scheme.n - lot.pick_n, pick_n=lot.pick_n, history_cutoffs=training_cutoffs(engine.data),
    )
    first, last = LR_RESTRICTED_RANGE if restricted else (1, lot.max_n)
    main_target = decision.game_hit_target(main.key, hit_target)
    urns = [
        _bench_urn(
            "Urna principală", matrix, main, scheme.n, tuple(range(first, min(last, lot.max_n) + 1)),
            scheme.n if inverse else 0, scheme.n, scheme, dynamic, lookback, main_target, cancel_path,
            calendar=dates is not None,
        ),
    ]
    if lot.bench_key_urna2:
        maximum = lot.geo.second.max_n
        if "joker" in engine.data:
            bonus_draws, valid = valid_draw_matrix(engine.data, ["joker"], draw_n=1, max_num=maximum)
            bounds = training_cutoffs(engine.data.loc[valid])
        else:
            bonus_draws, bounds = np.empty((0, 1), dtype=np.int32), ()
        bonus = GameDef(
            lot.bench_key_urna2, "Urna 2 Joker", "", [], maximum, 1,
            is_single_pick=True, history_cutoffs=bounds,
        )
        report = _bench_urn(
            "Urna 2 Joker", bonus_draws, bonus, 1, tuple(range(1, maximum + 1)),
            joker_size if inverse else 0, joker_size, None, False, lookback, 1, cancel_path,
            calendar=dates is not None,
        )
        if report["protocol"]:
            report["protocol"] += " Intervalul restrâns se aplică numai urnei principale. Urna 2 este evaluată la top-1."
        urns.append(report)
    portfolio = None
    if layout is not None:
        from loto_enterprise.benchmark.lr_budget_scoring import evaluate_budget_recipes
        portfolio = evaluate_budget_recipes(
            layout, scheme, lot, engine.data, matrix, dates, main, urns,
            restricted=restricted, inverse=inverse, dynamic=dynamic, lookback=lookback,
            joker_size=joker_size, check=check, recipe=recipe,
        )
        if error := portfolio.get("error"):
            # Preserve the pool diagnostics, but never publish them as a paid-ticket winner.
            for urn in urns:
                urn.update(decision=None, method=None)
                urn.setdefault("error", error)
    check(force=True)
    refresh_catalog(lr_schemes.LR_DIR)
    if lr_semantics(lot, scheme, hit_target, layout=layout) != semantics:
        raise ValueError("Regulile de selecție s-au schimbat în timpul calculului. Reia Re-Bench.")
    return {
        "game": game_id, "scheme_code": scheme.code,
        "semantics": semantics, "portfolio": portfolio,
        **({"portfolio_refinement": portfolio["portfolio_refinement"]} if portfolio and "portfolio_refinement" in portfolio else {}),
        "max_consecutive_run": LR_MAX_CONSECUTIVE_RUN, "restricted_range": list(LR_RESTRICTED_RANGE),
        "history_hash": engine.audit["hash"], "history_rows": len(matrix),
        "as_of": dates.iloc[-1].date().isoformat() if dates is not None and len(dates) else None,
        "inverse": bool(inverse), "restricted": bool(restricted), "lookback": lookback, "urns": urns,
    }
