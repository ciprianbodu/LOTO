"""Replay retrospectiv al selecției, fără decizii/cache/stări de producție.

Default: ultimele 180 de predicții pre-extragere, primele 120 disponibile
pentru selecție și ultimele 60 pentru evaluare temporală. Fiecare scorer vede
numai zile anterioare țintei; selecția vede numai rezultate din zile anterioare
primului target al blocului. Toate metodele sunt evaluate o singură dată.

Acesta NU este un holdout extern: formulele și registry-ul au fost dezvoltate
pe istoricul disponibil. Replay-ul separă selecția de evaluarea ulterioară,
dar nu poate anula această selecție retrospectivă a formulelor.
"""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import nullcontext
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
import hashlib
import json
import logging
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch

# Un proces per metodă; BLAS multithreading în fiecare proces ar suprascrie
# bugetul CPU al experimentului și ar face SVD-urile mici mult mai lente.
for _variable in ("OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "OMP_NUM_THREADS"):
    os.environ[_variable] = "1"

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from loto_enterprise.benchmark import decision
from loto_enterprise.benchmark.methods import METHODS, call_method
from loto_enterprise.benchmark.runner import GameDef, _align_urna2_with_engine, discover_games
from loto_enterprise.core.draw_validation import valid_draw_matrix
from loto_enterprise.core.history import chronological_history, history_dates, training_cutoffs
from loto_enterprise.core.ranking import limit_consecutive_run, rank_by_score
from loto_enterprise.core.score_validation import has_usable_score_variance


@dataclass
class PredictionSeries:
    method: str
    raw_hits: list[int]
    limited_hits: list[int]
    ties: list[bool]
    errors: list[dict]
    runtime_sec: float


def history_for_game(game: GameDef) -> tuple[np.ndarray, tuple[int, ...], list[str], str]:
    """Validează/sortează cu aceleași reguli ca runnerul pentru jocul principal."""
    source = Path(game.csv_path)
    frame = chronological_history(pd.read_csv(source))
    if game.is_single_pick:
        frame = _align_urna2_with_engine(frame)
    draws, valid = valid_draw_matrix(
        frame, game.cols, draw_n=game.draw_n, max_num=game.max_num
    )
    frame = frame.loc[valid].reset_index(drop=True)
    dates = history_dates(frame)
    labels = dates.dt.strftime("%Y-%m-%d").tolist() if dates is not None else [str(i) for i in range(len(frame))]
    return draws, training_cutoffs(frame), labels, hashlib.sha256(source.read_bytes()).hexdigest()


def evaluate_method(
    method: str, draws: np.ndarray, cutoffs: tuple[int, ...], indices: list[int],
    max_num: int, pool_size: int, holdout_start: int, max_run: int,
) -> PredictionSeries:
    """Scoruri pre-target pure; -1 înseamnă scor inutilizabil, nu zero hituri."""
    started = time.perf_counter()
    raw_hits, limited_hits, ties, errors = [], [], [], []
    # Două ținte în aceeași zi au obligatoriu același prefix și aceleași pooluri.
    previous_cutoff, previous = None, None
    for offset, index in enumerate(indices):
        cutoff = int(cutoffs[index])
        try:
            if cutoff != previous_cutoff:
                scores, _elapsed = call_method(method, draws[:cutoff], max_num)
                if not has_usable_score_variance(scores):
                    raise ValueError("unusable_scores")
                ranked = rank_by_score(scores, max_num)
                if len(ranked) < pool_size:
                    raise ValueError("incomplete_pool")
                ordered_values = [float(scores[number]) for number in ranked]
                tie = len(ranked) > pool_size and ordered_values[pool_size - 1] == ordered_values[pool_size]
                previous = (ranked, tie)
                previous_cutoff = cutoff
            ranked, tie = previous
            actual = set(map(int, draws[index]))
            raw_hits.append(len(set(ranked[:pool_size]) & actual))
            ties.append(bool(tie))
            if offset >= holdout_start:
                limited, applied, _skipped = limit_consecutive_run(ranked, pool_size, max_run)
                if max_run > 0 and applied != max_run:
                    raise ValueError("consecutive_limit_relaxed")
                limited_hits.append(len(set(limited) & actual))
            else:
                limited_hits.append(-1)
        except Exception as exc:
            # Necomparabilitatea se păstrează explicit și exclude selecția;
            # nu premiem o metodă care a omis tocmai predicțiile dificile.
            if len(raw_hits) == offset + 1:
                raw_hits[-1] = -1
                ties[-1] = False
            else:
                raw_hits.append(-1)
                ties.append(False)
            limited_hits.append(-1)
            errors.append({"index": int(index), "error": f"{type(exc).__name__}: {exc}"})
            previous_cutoff, previous = None, None
    return PredictionSeries(method, raw_hits, limited_hits, ties, errors, time.perf_counter() - started)


def available_prediction_offsets(indices: list[int], cutoff: int) -> list[int]:
    """Outcomes cunoscute înaintea țintei; exclude întreaga zi a acesteia."""
    return [offset for offset, index in enumerate(indices) if index < int(cutoff)]


def selection_frame(
    series: dict[str, PredictionSeries], available: list[int], game_key: str,
    pool_size: int, percentiles: tuple[int, ...] = (10, 30, 60, 100),
) -> pd.DataFrame:
    """Folduri cuibărite pe predicții anterioare, fără a re-score-a istoria."""
    rows = []
    for method, result in series.items():
        for percentile in percentiles:
            count = max(1, int(len(available) * percentile / 100))
            offsets = available[-count:]
            hits = np.asarray([result.raw_hits[i] for i in offsets], dtype=int)
            valid = hits >= 0
            evaluated = hits[valid]
            row = {
                "game": game_key, "method": method, "percentile": percentile,
                "is_random": False, "failed": len(evaluated) == 0,
                "n_test": len(offsets), "n_eval": len(evaluated), "runtime_sec": 0.0,
                f"k{pool_size}": float(evaluated.mean()) if len(evaluated) else np.nan,
                f"tiebreak_k{pool_size}": float(np.mean([result.ties[i] for i in offsets if result.raw_hits[i] >= 0])) if len(evaluated) else np.nan,
            }
            for threshold in (1, 3, 4, 5):
                row[f"rate_{threshold}plus_k{pool_size}"] = float(np.mean(evaluated >= threshold)) if len(evaluated) else np.nan
            rows.append(row)
    return pd.DataFrame(rows)


def select_from_past(
    series: dict[str, PredictionSeries], indices: list[int], cutoff: int,
    game: GameDef, pool_size: int, target: int,
) -> tuple[dict, list[int]]:
    available = available_prediction_offsets(indices, cutoff)
    if not available:
        raise ValueError("no prior predictions for selection")
    frame = selection_frame(series, available, game.key, pool_size)
    # Funcția e pură: nu apelează save_decision și nu citește folds/best_methods.
    target_context = patch.object(decision, "BENCH_HIT_TARGET", target) if game.draw_n > 1 else nullcontext()
    with target_context:
        config = decision.decide_optimal_config_for_pool(
            frame, game.key, pool_size, game.draw_n, game.max_num, game.pick_n or game.draw_n
        )
    if "error" in config:
        raise ValueError(config["error"])
    return config, available


def hit_summary(hits: list[int], max_num: int, draw_n: int, pool_size: int) -> dict:
    valid = np.asarray([value for value in hits if value >= 0], dtype=int)
    count = int(len(valid))
    summary = {"requested": len(hits), "evaluated": count, "missing": len(hits) - count}
    for threshold in (1, 3, 4, 5):
        successes = int(np.count_nonzero(valid >= threshold))
        baseline = decision.expected_random_rate(max_num, draw_n, pool_size, threshold)
        summary[f"hits_{threshold}plus"] = successes
        summary[f"rate_{threshold}plus"] = successes / count if count else None
        summary[f"baseline_{threshold}plus"] = baseline
        summary[f"expected_{threshold}plus"] = count * baseline
        summary[f"wilson95_lower_{threshold}plus"] = decision._wilson_lower_bound(successes, count, 1.96) if count else None
    summary["mean_hits"] = float(valid.mean()) if count else None
    return summary


def replay_scenario(
    series: dict[str, PredictionSeries], indices: list[int], cutoffs: tuple[int, ...],
    dates: list[str], game: GameDef, pool_size: int, holdout_start: int,
    target: int, block_size: int,
) -> dict:
    raw, limited, selections, traces = [], [], [], []
    stop = len(indices)
    for start in range(holdout_start, stop, block_size):
        target_index = indices[start]
        config, available = select_from_past(series, indices, cutoffs[target_index], game, pool_size, target)
        method = config["scorer"]
        result = series[method]
        end = min(stop, start + block_size)
        # Reselecția la bloc este conservatoare: toate alegerile blocului
        # rămân înghețate chiar dacă apar între timp outcomes noi.
        assert all(indices[i] < cutoffs[target_index] for i in available)
        selections.append({
            "start_index": target_index, "date": dates[target_index], "method": method,
            "prior_predictions": len(available), "last_selection_outcome_index": indices[available[-1]],
            "low_confidence": config.get("low_confidence"), "qualifying_count": config.get("qualifying_methods"),
            "target_metric": config.get("hit_target"), "rationale": config.get("rationale"),
        })
        for offset in range(start, end):
            index = indices[offset]
            raw.append(result.raw_hits[offset])
            limited.append(result.limited_hits[offset])
            traces.append({"index": index, "date": dates[index], "method": method,
                           "training_cutoff": cutoffs[index], "selection_cutoff": cutoffs[target_index],
                           "raw_hits": result.raw_hits[offset], "consecutive_hits": result.limited_hits[offset]})
    return {"target": target, "block_size": block_size, "selections": selections,
            "chosen_methods": dict(Counter(trace["method"] for trace in traces)),
            "raw": hit_summary(raw, game.max_num, game.draw_n, pool_size),
            "consecutive": hit_summary(limited, game.max_num, game.draw_n, pool_size), "trace": traces}


def run_replay(history_dir: Path, count: int = 180, holdout: int = 60,
               pool_size: int = 16, max_run: int = 2, workers: int = 4,
               block_size: int = 20, game_keys: tuple[str, ...] | None = None) -> dict:
    if not 0 < holdout < count or count - holdout < 30:
        raise ValueError("need at least 30 calibration predictions and a positive holdout")
    if workers < 1 or block_size < 1 or pool_size < 1 or max_run < 0:
        raise ValueError("workers, block_size and pool_size must be positive; max_run nonnegative")
    started = time.perf_counter()
    games = discover_games(str(history_dir))
    if game_keys is not None:
        unknown = set(game_keys) - {game.key for game in games}
        if unknown:
            raise ValueError(f"unknown games: {sorted(unknown)}")
        games = [game for game in games if game.key in game_keys]
    methods = sorted(METHODS)
    report = {"contract": {
        "selection_validation": "retrospective_temporal_selection_replay",
        "external_untouched_holdout": False, "pool_size": pool_size,
        "max_consecutive_run": max_run, "count": count, "holdout": holdout,
        "methods_count": len(methods), "workers": workers,
        "scorer_history": "all valid draws before target day; no penalty/restriction/feedback",
        "selection_history": "only pre-draw prediction outcomes before first target day in block",
        "selection_windows": [10, 30, 60, 100], "selection_wilson_z": 1.0,
        "targets": {"loto_6_49": [3, 4], "loto_5_40": [4], "joker_urna1": [3, 4], "joker_urna2": [1]},
        "five_plus": "diagnostic only; never used for method selection",
        "selection_pool": "raw top-N; consecutive restriction assessed in holdout only",
        "warning": "Current scorer formulas were designed using available history. Retrospective replay is not external validation. No promotion from this experiment.",
    }, "games": {}}
    for game in games:
        game_pool = 1 if game.is_single_pick else pool_size
        game_max_run = 0 if game.is_single_pick else max_run
        if game_pool > game.max_num:
            raise ValueError(f"pool too large for {game.key}")
        draws, cutoffs, dates, source_hash = history_for_game(game)
        indices = [i for i in range(max(0, len(draws) - count), len(draws)) if cutoffs[i] >= 5]
        if len(indices) != count:
            raise ValueError(f"{game.key}: need {count} eligible predictions")
        holdout_start = count - holdout
        series = {}
        print(f"{game.key}: {len(methods)} methods × {count} pre-draw predictions", file=sys.stderr, flush=True)
        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(evaluate_method, method, draws, cutoffs, indices,
                                       game.max_num, game_pool, holdout_start, game_max_run): method for method in methods}
            for completed, future in enumerate(as_completed(futures), 1):
                result = future.result()
                series[result.method] = result
                if completed % 13 == 0 or completed == len(methods):
                    print(f"  {completed}/{len(methods)} methods; elapsed {time.perf_counter()-started:.1f}s", file=sys.stderr, flush=True)
        targets = (1,) if game.is_single_pick else ((4,) if game.key == "loto_5_40" else (3, 4))
        scenarios = []
        for target in targets:
            for block in (holdout, block_size):
                scenarios.append(replay_scenario(series, indices, cutoffs, dates, game, game_pool,
                                                 holdout_start, target, block))
        baseline_methods = {}
        for method in ("frequency", "random"):
            result = series[method]
            baseline_methods[method] = {
                "raw": hit_summary(result.raw_hits[holdout_start:], game.max_num, game.draw_n, game_pool),
                "consecutive": hit_summary(result.limited_hits[holdout_start:], game.max_num, game.draw_n, game_pool),
            }
        report["games"][game.key] = {
            "source": str(Path(game.csv_path).resolve()), "source_sha256": source_hash,
            "draw_n": game.draw_n, "max_num": game.max_num, "pool_size": game_pool,
            "max_consecutive_run": game_max_run, "predictions": count,
            "start_date": dates[indices[0]], "holdout_start_date": dates[indices[holdout_start]],
            "end_date": dates[indices[-1]], "baselines": baseline_methods, "scenarios": scenarios,
            "method_holdout": {
                name: {"raw": hit_summary(result.raw_hits[holdout_start:], game.max_num, game.draw_n, game_pool),
                       "consecutive": hit_summary(result.limited_hits[holdout_start:], game.max_num, game.draw_n, game_pool)}
                for name, result in sorted(series.items())
            },
            "method_errors": {name: result.errors for name, result in sorted(series.items()) if result.errors},
            "method_runtime_seconds": {name: round(result.runtime_sec, 3) for name, result in sorted(series.items())},
        }
    report["runtime_sec"] = round(time.perf_counter() - started, 3)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history-dir", type=Path, default=ROOT / "_ISTORIC")
    parser.add_argument("--count", type=int, default=180)
    parser.add_argument("--holdout", type=int, default=60)
    parser.add_argument("--pool-size", type=int, default=16)
    parser.add_argument("--max-run", type=int, default=2)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--block-size", type=int, default=20)
    parser.add_argument("--games", nargs="+", choices=("loto_6_49", "loto_5_40", "joker_urna1", "joker_urna2"))
    parser.add_argument("--output", type=Path, help="explicit audit artifact only; never production state")
    args = parser.parse_args()
    logging.basicConfig(level=logging.ERROR)
    report = run_replay(args.history_dir, args.count, args.holdout, args.pool_size,
                        args.max_run, args.workers, args.block_size, tuple(args.games) if args.games else None)
    body = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        from ui_shared import atomic_write_text
        atomic_write_text(args.output, body)
        print(str(args.output.resolve()))
    else:
        print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
