"""Compare percentages of the strictly prior validation history on common future draws.

Run from the repository with python -m scripts.analysis.validation_window_study_2026_11_18
--output <new JSON path>. Results are exploratory because the archives have been reused.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
from hashlib import sha256
import json
from pathlib import Path

import numpy as np
import pandas as pd

from covering.lr_selection import resolve_lr_scheme
from loto_enterprise.benchmark.methods import call_method
from loto_enterprise.core.draw_validation import valid_draw_matrix
from loto_enterprise.core.history import chronological_history, history_dates, training_cutoffs
from loto_enterprise.core.lotteries import GAMES
from loto_enterprise.core.pool_selection import select_pool_from_scores
from loto_enterprise.core.score_validation import has_usable_score_variance
from scripts.analysis.lr_pool_study_2026_11_04 import exact_lookup


ROOT = Path(__file__).resolve().parents[2]
WINDOWS = (100, 70, 30, 10)  # ties prefer the longest window
METHODS = (
    'frequency', 'freq_window_50', 'freq_window_200', 'momentum_30_300',
    'cusum_burst', 'runs_persistence', 'spectral_phase', 'repeat_last_draw',
    'neighbor_adjacent',
)
LOCAL_INCUMBENTS = {'loto_6_49': 'naive_bayes_last', 'loto_5_40': 'croston_interval',
                    'joker_urna2': 'ridge_pooled_feats'}
LOCAL_CODES = {'loto_6_49': ('6/49', 48, 9), 'loto_5_40': ('5/40', 16, 8),
               'joker_urna1': ('joker', 24, 10)}
WARMUP_ROWS = 250
MIN_VALIDATION_DAYS = 100
PROTOCOL = {
    'fractions_desc': WINDOWS, 'methods_in_tie_order': METHODS, 'extra_candidates': LOCAL_INCUMBENTS,
    'scorer_warmup_rows': WARMUP_ROWS, 'minimum_prior_validation_days': MIN_VALIDATION_DAYS,
    'window_definition': 'Trailing ceil(pct * prior eligible distinct dates / 100) complete dates. '
                         'Each scored draw has weight one. Scorers train on all strictly earlier dates.',
    'geometry': 'Current Romanian official codes 48/24/16. Foreign main pools K10, simulated '
                'positional reference Cod49 for six-ball tickets and Cod24 for five-ball tickets. '
                'Romanian Joker second drum is top-1. EuroMillions stars are not modelled.',
    'choice': 'Daily causal selection among the same registered scorer candidates for every fraction. '
              'Lexicographic event counts 5+, 4+, sum of hits, ties use declared scorer order. '
              'Single pick uses successful top-1 predictions. Repeat using the best ticket as selection target.',
    'evaluation': 'After scorer warmup and 100 prior eligible dates, first 70% of remaining dates '
                  'choose one window per game and objective. Evaluate all windows and the frozen choice '
                  'on the identical last 30% dates. Longer window wins a validation tie.',
    'comparison': 'Paired discordances and terminal e-values for fixed and development-selected windows '
                  'against 100% under uniform independent main draws. Mixture over bets .05/.15/.35/.60 '
                  'on +1/-1 per discordance; equal geometry makes the signed difference conditionally fair '
                  'under that null. Evidence threshold 1/alpha per hypothesis times family size, including '
                  'three fixed alternatives and one frozen choice per objective and stream, counting the '
                  'single-pick objective only once. Exploratory archive; no deployment threshold inferred.',
}
BET_FRACTIONS = np.array([0.05, 0.15, 0.35, 0.60])


def load_stream(lottery, key):
    path = ROOT / lottery.csv
    frame = chronological_history(pd.read_csv(path)).reset_index(drop=True)
    second = key == 'joker_urna2'
    universe = 20 if second else lottery.max_n
    draw_n = 1 if second else lottery.draw_n
    columns = ['joker'] if second else [f'n{i}' for i in range(1, draw_n + 1)]
    matrix, valid = valid_draw_matrix(frame, columns, draw_n=draw_n, max_num=universe)
    if not np.all(valid):
        raise ValueError(f'{key} has invalid rows')
    dates = history_dates(frame).to_numpy().astype('datetime64[D]')
    cutoffs = np.asarray(training_cutoffs(frame), dtype=np.int64)
    eligible = np.flatnonzero((cutoffs == np.arange(len(matrix))) & (cutoffs >= WARMUP_ROWS))
    if len(eligible) <= MIN_VALIDATION_DAYS + 2:
        raise ValueError(f'{key} has insufficient dated history')
    return matrix, dates, cutoffs, eligible, universe, sha256(path.read_bytes()).hexdigest()


def scheme_for(lottery, key):
    if key == 'joker_urna2':
        return 1, None, np.array([0, 1])
    reference = ('6/49', 49, 10) if lottery.pick_n == 6 else ('joker', 24, 10)
    label, code, k = LOCAL_CODES.get(key, reference)
    scheme = resolve_lr_scheme(label, code, k)
    return k, {'label': label, 'code': code, 'variants': scheme.size,
               'official_for_game': lottery.is_romanian}, exact_lookup(k, scheme.variants)


def trajectories(matrix, cutoffs, days, universe, k, lookup, methods):
    first = int(days[0])
    shape = (len(matrix) - first, len(methods))
    pool = np.empty(shape, dtype=np.uint8)
    ticket = np.empty_like(pool)
    fallbacks = dict.fromkeys(methods, 0)
    for start, end in zip(days, np.r_[days[1:], len(matrix)], strict=True):
        prefix = matrix[:start]
        frequency = None
        positions = np.zeros((len(methods), universe + 1), dtype=np.int32)
        for i, method in enumerate(methods):
            scores, _ = call_method(method, prefix, universe, history_cutoffs=cutoffs[:start])
            if not has_usable_score_variance(scores):
                if frequency is None:
                    raise ValueError('Unusable frequency fallback')
                scores = frequency
                fallbacks[method] += 1
            if i == 0:
                frequency = scores
            chosen = select_pool_from_scores(scores, k, set(), max_num=universe, max_consecutive_run=2)
            if len(chosen) != k:
                raise ValueError('Incomplete pool')
            positions[i, chosen] = 1 << np.arange(k)
        for row in range(start, end):
            masks = positions[:, matrix[row]].sum(axis=1)
            pool[row - first] = np.bitwise_count(masks.astype(np.uint32))
            ticket[row - first] = lookup[masks]
    return pool, ticket, fallbacks


def totals(hits, threshold):
    if threshold == 1:
        return np.asarray([hits.sum(axis=0, dtype=np.int64)])
    return np.stack(((hits >= 5).sum(axis=0), (hits >= 4).sum(axis=0),
                     hits.sum(axis=0, dtype=np.int64)))


def best_index(values):
    """Columns are policies in declared tie order. Rows are descending priorities."""
    return max(range(values.shape[1]), key=lambda i: tuple(values[:, i]))


def selected_trajectories(pool, ticket, day_starts, target, threshold):
    series = pool if target == 'pool' else ticket
    measures = (series,) if threshold == 1 else (series >= 5, series >= 4, series)
    prefixes = np.stack([np.vstack((np.zeros((1, h.shape[1]), dtype=np.int64),
                                    np.cumsum(h, axis=0, dtype=np.int64))) for h in measures])
    selected = np.full((len(series), len(WINDOWS)), -1, dtype=np.int16)
    for i in range(MIN_VALIDATION_DAYS, len(day_starts)):
        start = int(day_starts[i])
        end = int(day_starts[i + 1]) if i + 1 < len(day_starts) else len(series)
        for w, pct in enumerate(WINDOWS):
            span = (pct * i + 99) // 100
            left = day_starts[i - span]
            selected[start:end, w] = best_index(prefixes[:, start] - prefixes[:, left])
    return selected


def summarize(pool, ticket, selection_metric, threshold):
    target = pool if selection_metric == 'pool' else ticket
    base = target[:, 0] >= threshold
    result = {}
    for i, pct in enumerate(WINDOWS):
        candidate = target[:, i] >= threshold
        wins = int(np.sum(candidate & ~base))
        losses = int(np.sum(base & ~candidate))
        value = np.exp(wins * np.log1p(BET_FRACTIONS) + losses * np.log1p(-BET_FRACTIONS)).mean()
        result[str(pct)] = {
            **{f'pool_{t}plus': int(np.sum(pool[:, i] >= t)) for t in (1, 4, 5, 6)},
            **{f'ticket_{t}plus': int(np.sum(ticket[:, i] >= t)) for t in (1, 4, 5, 6)},
            'pool_sum': int(pool[:, i].sum()), 'ticket_sum': int(ticket[:, i].sum()),
            'paired_wins': wins, 'paired_losses': losses, 'e_vs_100': float(value),
        }
    return result


def run_game(key):
    lottery = next(lot for lot in GAMES if key in lot.bench_keys)
    matrix, dates, cutoffs, days, universe, digest = load_stream(lottery, key)
    k, scheme, lookup = scheme_for(lottery, key)
    methods = METHODS + ((LOCAL_INCUMBENTS[key],) if key in LOCAL_INCUMBENTS else ())
    pool, ticket, unused = trajectories(matrix, cutoffs, days, universe, k, lookup, methods)
    offsets = days - days[0]
    first = int(offsets[MIN_VALIDATION_DAYS])
    n_days = len(days) - MIN_VALIDATION_DAYS
    split = int(offsets[MIN_VALIDATION_DAYS + n_days * 7 // 10])
    idx = np.arange(first, len(pool))[:, None]
    result = {
        'game': key, 'history_sha256': digest, 'history_rows': len(matrix), 'pool_size': k,
        'scheme': scheme, 'scorers': methods, 'fallback_days': unused, 'validation_days': len(days),
        'development_n': split - first, 'holdout_n': len(pool) - split,
        'development_start': str(dates[first + days[0]]), 'holdout_start': str(dates[split + days[0]]),
        'holdout_end': str(dates[-1]), 'holdout_days': n_days - n_days * 7 // 10, 'objectives': {},
    }
    threshold = 1 if k == 1 else 5
    for goal in (('pool',) if k == 1 else ('pool', 'ticket')):
        selected = selected_trajectories(pool, ticket, offsets, goal, threshold)[first:]
        if np.any(selected < 0):
            raise ValueError('A selection is missing')
        pools, tickets = pool[idx, selected], ticket[idx, selected]
        split_rel = split - first
        dev = (pools if goal == 'pool' else tickets)[:split_rel]
        winner = WINDOWS[best_index(totals(dev, threshold))]
        result['objectives'][goal] = {
            'frozen_pct': winner,
            'development': summarize(pools[:split_rel], tickets[:split_rel], goal, threshold),
            'holdout': summarize(pools[split_rel:], tickets[split_rel:], goal, threshold),
        }
    print(f'{key}: {result["holdout_n"]} holdout rows', flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--protocol-only', action='store_true')
    args = parser.parse_args()
    if args.protocol_only:
        args.output.write_text(json.dumps(PROTOCOL, indent=2), encoding='utf-8')
        return
    keys = [key for lot in GAMES for key in lot.bench_keys]
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        games = list(executor.map(run_game, keys))
    family = 4 * sum(len(g['objectives']) for g in games)
    output = {'protocol': PROTOCOL, 'games': games, 'family_size': family,
              'evidence_threshold': family / 0.05}
    args.output.write_text(json.dumps(output, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
