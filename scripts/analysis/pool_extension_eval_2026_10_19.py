"""Canonical evaluation for the separate 5/40 and Joker pool study.

Each requested pool size calls the application's consecutive-limit helper
separately. No nesting assumption is required, including for future restricted
universes. All finite ball scores are ranked canonically without a frequency
tie-break. This module neither fits a method nor changes application state.
"""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from numbers import Integral
from pathlib import Path
import multiprocessing
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from loto_enterprise.core.pool_selection import apply_consecutive_limit
from loto_enterprise.core.ranking import rank_by_score
from loto_enterprise.core.score_validation import has_usable_score_variance


def _geometry(max_num, draw_n, ks):
    if (
        isinstance(max_num, (bool, np.bool_))
        or not isinstance(max_num, Integral)
        or max_num < 1
        or isinstance(draw_n, (bool, np.bool_))
        or not isinstance(draw_n, Integral)
        or not 1 <= draw_n <= max_num
    ):
        raise ValueError("max_num and draw_n must describe a valid integer universe")
    ks = tuple(ks)
    if (
        not ks
        or any(
            isinstance(k, (bool, np.bool_))
            or not isinstance(k, Integral)
            or not 1 <= k <= max_num
            for k in ks
        )
        or len(set(ks)) != len(ks)
    ):
        raise ValueError("ks must contain distinct integer sizes in 1..max_num")
    return int(max_num), int(draw_n), tuple(map(int, ks))


def evaluate_scores(scores, draws, *, max_num, draw_n, ks):
    """Return hits[T,2,K] (raw, consecutive2) and raw boundary ties[T,K].

    draw_n counts actual drawn balls, independently of the physical ticket size.
    Thus 5/40 supplies all SIX drawn balls, while Joker main supplies five.
    The column order is exactly the supplied order of distinct pool sizes.
    """
    max_num, draw_n, ks = _geometry(max_num, draw_n, ks)
    scores = np.asarray(scores, dtype=np.float64)
    try:
        numeric_draws = np.asarray(draws, dtype=np.float64)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("draws must contain finite integer labels") from exc
    if scores.ndim != 2 or scores.shape[1] != max_num:
        raise ValueError(f"scores must have shape [T,{max_num}]")
    if numeric_draws.shape != (len(scores), draw_n):
        raise ValueError(f"draws must have shape [T,{draw_n}]")
    if not np.isfinite(scores).all():
        raise ValueError("unusable nonfinite scores")
    if (
        not np.isfinite(numeric_draws).all()
        or not ((numeric_draws >= 1) & (numeric_draws <= max_num)).all()
        or not np.equal(numeric_draws, np.floor(numeric_draws)).all()
    ):
        raise ValueError("draw labels must be finite integers in 1..max_num")
    integer_draws = numeric_draws.astype(np.int64)
    if np.any(np.diff(np.sort(integer_draws, axis=1), axis=1) == 0):
        raise ValueError("each draw must contain distinct balls")
    hits = np.zeros((len(scores), 2, len(ks)), dtype=np.int8)
    ties = np.zeros((len(scores), len(ks)), dtype=bool)
    for row_index, (row, draw) in enumerate(zip(scores, integer_draws, strict=True)):
        mapping = dict(enumerate(map(float, row), start=1))
        if not has_usable_score_variance(mapping):
            raise ValueError(f"unusable flat scores at row {row_index}")
        ranked = rank_by_score(mapping, max_num)
        actual = set(map(int, draw))
        for column, k in enumerate(ks):
            raw = ranked[:k]
            constrained = apply_consecutive_limit(ranked, k, 2)
            hits[row_index, 0, column] = len(actual.intersection(raw))
            hits[row_index, 1, column] = len(actual.intersection(constrained))
            if k < max_num:
                ties[row_index, column] = (
                    abs(row[ranked[k - 1] - 1] - row[ranked[k] - 1]) <= 1e-12
                )
    return hits, ties


def _evaluate_one(item):
    name, scores, invalid, draws, max_num, draw_n, ks = item
    invalid = np.asarray(invalid)
    if invalid.shape != (len(scores),) or invalid.dtype != np.bool_:
        raise ValueError("invalid must be a boolean vector aligned with score rows")
    hits, ties = evaluate_scores(scores, draws, max_num=max_num, draw_n=draw_n, ks=ks)
    return name, hits, ties, invalid


def evaluate_bank(items, draws, *, max_num, draw_n, ks, workers=4):
    """Stream (name,hits,ties,invalid), with at most 2*workers pending matrices.

    items contains (name,scores[T,max_num],invalid[T]). Completion order can
    differ in parallel; callers align results by name. A worker child stays
    serial, and the requested concurrency is capped at four CPU processes.
    """
    max_num, draw_n, ks = _geometry(max_num, draw_n, ks)
    if isinstance(workers, bool) or not isinstance(workers, Integral) or workers < 1:
        raise ValueError("workers must be a positive integer")
    workers = min(int(workers), 4)
    if multiprocessing.current_process().name != "MainProcess":
        workers = 1
    if workers == 1:
        for name, scores, invalid in items:
            yield _evaluate_one((name, scores, invalid, draws, max_num, draw_n, ks))
        return
    iterator = iter(items)
    pending = set()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        exhausted = False
        while pending or not exhausted:
            while not exhausted and len(pending) < 2 * workers:
                try:
                    name, scores, invalid = next(iterator)
                except StopIteration:
                    exhausted = True
                    break
                pending.add(pool.submit(_evaluate_one, (name, scores, invalid, draws, max_num, draw_n, ks)))
            if pending:
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    yield future.result()
