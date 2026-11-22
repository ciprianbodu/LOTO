"""Score an inverse pool with the ordinary Re-Bench decision on eligible numbers."""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
from operator import index

import numpy as np
import pandas as pd

from loto_enterprise.benchmark import VALIDATION_PERCENTILES, decision
from loto_enterprise.benchmark.curated import apply_curation, resolve_methods_per_game
from loto_enterprise.benchmark.methods import call_method, list_methods, method_meta
from loto_enterprise.benchmark.runner import GameDef, _evaluate_fold, fold_sizes
from loto_enterprise.core.lotteries import lottery_by_bench_key
from loto_enterprise.core.method_selector import combine_ensemble_scores


def restricted_bench_scores(matrix, cutoffs, game_key, pool_size, excluded_numbers, hit_target=None):
    """Evaluate the same excluded set at every origin. Never publish the resulting decision."""
    lot = lottery_by_bench_key(game_key)
    if lot is None or not lot.is_romanian:
        raise ValueError(f"Joc LR necunoscut: {game_key}")
    bonus = game_key == lot.bench_key_urna2
    draw_n, pick_n, maximum = (1, 1, lot.geo.second.max_n) if bonus else (lot.draw_n, lot.pick_n, lot.max_n)
    excluded = tuple(sorted(set(map(index, excluded_numbers))))
    pool_size = index(pool_size)
    if not pick_n <= pool_size <= maximum - len(excluded) or any(n < 1 or n > maximum for n in excluded):
        raise ValueError("Pool-ul cerut nu încape în numerele eligibile ale urnei.")
    draws = np.asarray(matrix)
    if draws.ndim == 1 and bonus:
        draws = draws.reshape(-1, 1)
    if (draws.ndim != 2 or draws.shape[1] != draw_n or len(draws) == 0
            or not np.all((draws >= 1) & (draws <= maximum) & (draws == np.floor(draws)))
            or np.any(np.diff(np.sort(draws, axis=1), axis=1) == 0)):
        raise ValueError("Istoricul pentru Re-Bench este invalid.")
    bounds = tuple(map(index, cutoffs))
    if (len(bounds) != len(draws) or bounds[0] != 0
            or any(b != a and b != i for i, (a, b) in enumerate(zip(bounds, bounds[1:]), 1))):
        raise ValueError("Limitele zilelor nu corespund istoricului pentru Re-Bench.")
    candidates, _ = apply_curation(m for m in list_methods() if method_meta(m).get("available", True))
    methods = resolve_methods_per_game(candidates, [game_key]).get(game_key, candidates)
    target = 1 if bonus else decision.game_hit_target(
        game_key, decision.BENCH_HIT_TARGET if hit_target is None else hit_target,
    )
    scores, selection = _restricted_decision(
        np.ascontiguousarray(draws, dtype=np.int32).tobytes(), bounds, game_key,
        draw_n, pick_n, maximum, pool_size, excluded, tuple(sorted(methods)), target,
    )
    return dict(scores), deepcopy(selection)


@lru_cache(maxsize=24)
def _restricted_decision(data, bounds, game_key, draw_n, pick_n, maximum, size, excluded, methods, target):
    draws = np.frombuffer(data, dtype=np.int32).reshape(-1, draw_n)
    game = GameDef(game_key, game_key, "", [], maximum, draw_n, pool_extra=size - pick_n,
                   is_single_pick=draw_n == 1, pick_n=pick_n, history_cutoffs=bounds)
    blocked = frozenset(excluded)
    rows = []

    # A method sees the same prefix in overlapping windows. Keep only its scores
    # for the duration of that method's folds, without sharing state across jobs.
    def eligible_scores(name, history, universe, **context):
        scores, duration = call_method(name, history, universe, **context)
        return {n: score for n, score in scores.items() if n not in blocked}, duration

    for method in methods:
        prefix_scores = {}

        def score_prefix(name, history, universe, **context):
            end = len(history)
            if end not in prefix_scores:
                prefix_scores[end] = eligible_scores(name, history, universe, **context)
            return prefix_scores[end]

        for percentile in VALIDATION_PERCENTILES:
            train, test = fold_sizes(len(draws), percentile)
            if train < 80 or test < 1:
                continue
            fold, _ = _evaluate_fold(method, draws[:train], draws[train:], game, 1, score_method=score_prefix)
            fold.percentile = percentile
            rows.append(fold.as_record())
    config = {}
    if rows:
        config = decision.decide_optimal_config_for_pool(
            pd.DataFrame(rows), game_key, size, draw_n, maximum, pick_n, hit_target=target, candidate_methods=methods,
        )
        if config.get("error"):
            raise ValueError(f"Re-Bench pe numerele eligibile: {config['error']}")
    members = config.get("ensemble") or [{"method": config.get("scorer", "frequency"), "weight": 1.0}]
    contributions = [
        (member["method"], eligible_scores(member["method"], draws, maximum, history_cutoffs=bounds)[0], member["weight"])
        for member in members
    ]
    audit = {}
    scores = combine_ensemble_scores(contributions, audit)
    active = [name for name, _ in audit.get("ensemble_active", ())]
    fallback = bool(config.get("fallback") or not rows)
    if not scores:
        scores, _ = eligible_scores("frequency", draws, maximum)
        active, fallback = ["frequency"], True
    return scores, {
        "method": " + ".join(active), "source": "restricted_rebench", "fallback": fallback,
        "no_decision": not rows, "hit_target": target, "pool_hint": size,
    }
