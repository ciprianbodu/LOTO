"""Choose the next Romanian LR main-urn scorer from completed forecast days.

The live single-origin API and batched walk-forward API implement the same
selection. Nothing is fitted on a target's date. Repeated draws on a date have
one pool per method, and count as distinct outcomes on that main-number pool.
"""

from __future__ import annotations

import logging
from collections import deque

import numpy as np

from loto_enterprise.benchmark.methods import (
    METHODS,
    METHOD_LOAD_ERRORS,
    call_method,
    score_frequency,
)
from loto_enterprise.core.dynamic_pools import ranked_pools
from loto_enterprise.core.lotteries import ro_lottery_for_geometry
from loto_enterprise.core.score_validation import SCORE_VARIANCE_EPSILON

logger = logging.getLogger(__name__)

DYNAMIC_VERSION = "dynamic-lr-v4-compact-pool-4plus"
POOL_HIT_TARGET = 4
WARMUP_DRAWS = 250
WINDOW_DAYS = 104
MIN_DAYS = 26
# Fixed before replay: the random scorer is the last row, a comparator only.
CANDIDATES = ("frequency", *(name for name in METHODS if name.startswith("compact_")))


def _method_rows(history, bounds, universe, reported, candidates, scorer):
    """Return candidate and random scores, and a fallback flag for each candidate."""
    registered = []
    unavailable = []
    for method in (*candidates, "random"):
        try:
            scores, _ = scorer(method, history, universe, history_cutoffs=bounds)
            row = np.array([scores[i] for i in range(1, universe + 1)], dtype=float)
            if len(scores) != universe or not np.isfinite(row).all() or np.any(row < 0):
                raise ValueError("scoruri incomplete, negative sau nefinite")
            # A flat frequency forecast still defines the canonical tie order.
            usable = method == "frequency" or np.ptp(row) > SCORE_VARIANCE_EPSILON
            if not usable:
                raise ValueError("scoruri plate")
        except Exception as exc:
            if method in ("frequency", "random"):
                raise  # never replace genuine reference scores in a comparison
            if method not in reported:
                logger.warning(
                    "Selecție dinamică: %s folosește frequency la %d rânduri (%s)",
                    method,
                    len(history),
                    exc,
                )
                reported.add(method)
            row, usable = registered[0], False
        registered.append(row)
        unavailable.append(not usable)
    return np.stack(registered), np.asarray(unavailable[:-1], dtype=bool)


def _score_rows(history, bounds, universe, reported):
    return _method_rows(history, bounds, universe, reported, CANDIDATES, call_method)


def _winner(totals, days, thresholds):
    if days < MIN_DAYS:
        return 0, "insufficient_history"
    # A draw with 4, 5 or 6 main numbers in the proposed pool counts once.
    counts = totals[:, thresholds.index(POOL_HIT_TARGET)]
    best = int(np.argmax(counts[:-1]))  # First declared method wins an exact tie.
    if counts[best] <= counts[-1]:
        return 0, "no_gain_over_random"
    return best, "winner"


def _counts(row, thresholds):
    return {str(t): int(n) for t, n in zip(thresholds, row, strict=True)}


def dynamic_forecasts(
    matrix,
    cutoffs,
    scheme,
    origins,
    *,
    max_run=0,
    restrict_min=0,
    restrict_max=0,
    penalty_draws=0,
    penalty_factor=0.5,
    should_cancel=None,
):
    """Return {exclusive day boundary: (raw live scores, selection evidence)}.

    matrix contains 1-based main draw numbers. cutoffs[i] is the first row
    of i's date. origins are entire prior prefixes or the next live draw.
    The history passed in already respects the caller's scoring lookback.
    When cancelled, only fully prepared forecast origins are returned.
    """
    if (
        len(CANDIDATES) != 4
        or METHOD_LOAD_ERRORS
        or not all(name in METHODS for name in (*CANDIDATES, "random"))
    ):
        reason = METHOD_LOAD_ERRORS.get("methods_compact") or "înregistrare incompletă"
        raise RuntimeError(f"Catalogul compact LR este indisponibil: {reason}")
    return _forecast_origins(
        matrix,
        cutoffs,
        scheme,
        origins,
        candidates=CANDIDATES,
        select_winner=_winner,
        selection_metadata={
            "version": DYNAMIC_VERSION,
            "hit_scope": "pool",
            "pool_hit_target": POOL_HIT_TARGET,
            "pool_size": scheme.n,
            "candidate_methods": list(CANDIDATES),
            "control_method": "random",
        },
        max_run=max_run,
        restrict_min=restrict_min,
        restrict_max=restrict_max,
        penalty_draws=penalty_draws,
        penalty_factor=penalty_factor,
        should_cancel=should_cancel,
    )


def _forecast_origins(
    matrix,
    cutoffs,
    scheme,
    origins,
    *,
    candidates,
    select_winner,
    selection_metadata,
    archived_ticket_hits=None,
    prepare_archived_scores=None,
    max_run=0,
    restrict_min=0,
    restrict_max=0,
    penalty_draws=0,
    penalty_factor=0.5,
    should_cancel=None,
):
    """Replay causal pool forecasts; archived reports may supply their original ticket scorer."""
    lot = ro_lottery_for_geometry(scheme.game)
    if lot is None:
        raise ValueError("Selecția dinamică necesită o schemă oficială românească")
    universe = lot.max_n
    values = np.asarray(matrix)
    bounds = np.asarray(cutoffs)
    if (
        values.ndim != 2
        or values.shape[1] != lot.draw_n
        or bounds.shape != (len(values),)
        or (len(values) and bounds.dtype.kind not in "iu")
    ):
        raise ValueError("Istoricul și limitele zilelor trebuie să fie aliniate")
    requested = sorted(set(origins))
    if not requested or (should_cancel is not None and should_cancel()):
        return {}
    for start in requested:
        if (
            not isinstance(start, (int, np.integer))
            or not 0 <= start <= len(values)
            or (start < len(values) and bounds[start] != start)
        ):
            raise ValueError("Originea trebuie să fie înaintea primei extrageri din zi")
    end = int(requested[-1])
    values = values[:end]
    if (
        values.dtype.kind not in "iuf"
        or not np.all((values >= 1) & (values <= universe))
        or np.any(values != np.floor(values))
        or np.any(np.diff(np.sort(values, axis=1), axis=1) == 0)
    ):
        raise ValueError("Numere principale invalide în istoricul dinamic")
    matrix = np.ascontiguousarray(values, dtype=np.int64)
    bounds = bounds[:end].astype(np.int64, copy=False)
    indices = np.arange(end)
    all_starts = np.flatnonzero(bounds == indices)
    if end and (
        not len(all_starts)
        or all_starts[0] != 0
        or not np.array_equal(
            bounds, np.maximum.accumulate(np.where(bounds == indices, indices, 0))
        )
    ):
        raise ValueError("Limitele din istoric nu respectă ordinea zilelor")

    low = max(1, int(restrict_min or 1))
    high = min(universe, int(restrict_max)) if restrict_max > 0 else universe
    if high - low + 1 < lot.pick_n:
        low, high = 1, universe  # Same ignored invalid interval as the live engine.
    if high - low + 1 < scheme.n:
        raise ValueError(
            f"Cod {scheme.code} cere {scheme.n} numere în intervalul permis"
        )
    maximum_hits = lot.draw_n if archived_ticket_hits is None else lot.pick_n
    thresholds = tuple(range(4, maximum_hits + 1))
    days = all_starts[all_starts >= WARMUP_DRAWS]
    ends = np.r_[days[1:], end] if len(days) else np.empty(0, dtype=int)
    recorded = np.zeros(len(days), dtype=bool)
    for stop in np.searchsorted(days, requested):
        recorded[max(0, stop - WINDOW_DAYS) : stop] = True

    bank_size = len(candidates)
    totals = np.zeros((bank_size + 1, len(thresholds)), dtype=np.int64)
    recent_days = deque()
    evaluation_days = np.flatnonzero(recorded)
    day_indices = dict(
        zip(days[evaluation_days].tolist(), evaluation_days.tolist(), strict=True)
    )
    wanted = set(requested)
    # The archived trajectory scorer needs a final sentinel for an unseen row.
    indicators = np.zeros((end + 1, universe), dtype=np.uint8)
    indicators[indices[:, None], matrix - 1] = 1
    score_rows = (
        prepare_archived_scores(indicators, universe)
        if prepare_archived_scores is not None and day_indices
        else _score_rows
    )
    results = {}
    reported = set()
    for start in sorted(wanted | day_indices.keys()):
        if should_cancel is not None and should_cancel():
            break
        previous_days = int(np.searchsorted(days, start))
        first = max(0, previous_days - WINDOW_DAYS)
        n_days = previous_days - first
        while recent_days and recent_days[0][0] < first:
            _, expired = recent_days.popleft()
            totals -= expired
        scores = unavailable = None
        if start in day_indices or (start in wanted and n_days >= MIN_DAYS):
            scores, unavailable = score_rows(
                matrix[:start], bounds[:start], universe, reported
            )
        if start in wanted:
            selected, reason = select_winner(totals, n_days, thresholds)
            chosen = selected
            if unavailable is not None and unavailable[selected] and selected != 0:
                chosen, reason = 0, "unusable_current_scores"
            if scores is None:
                forecast = score_frequency(matrix[:start], universe)
            else:
                forecast = dict(enumerate(scores[chosen].tolist(), start=1))
            details = {
                **selection_metadata,
                "method": candidates[chosen],
                "weights": {},
                "selected_method": candidates[selected],
                "reason": reason,
                "candidate_count": bank_size,
                "history_rows": int(start),
                "window_days": WINDOW_DAYS,
                "validation_days": n_days,
                "validation_draws": int(start - days[first]) if n_days else 0,
                "hits": _counts(totals[chosen], thresholds),
                "random_hits": _counts(totals[-1], thresholds),
            }
            results[int(start)] = (forecast, details)
        if start in day_indices:
            day = day_indices[start]
            pools = ranked_pools(
                scores,
                pool_size=scheme.n,
                universe=universe,
                min_ball=low,
                max_ball=high,
                max_run=max_run,
                past_draws=matrix[:start],
                penalty_draws=penalty_draws,
                penalty_factor=penalty_factor,
            )
            columns = pools.astype(np.intp) - 1
            daily = np.zeros_like(totals)
            for draw in indicators[start : ends[day]]:
                matches = draw[columns]
                hits = (
                    matches.sum(axis=1)
                    if archived_ticket_hits is None
                    else archived_ticket_hits(matches)
                )
                daily += hits[:, None] >= thresholds
            recent_days.append((day, daily))
            totals += daily
    return results


def choose_dynamic_winner(matrix, cutoffs, scheme, **settings):
    """Select after the latest completed draw date, including live and uploads."""
    end = len(matrix)
    return dynamic_forecasts(matrix, cutoffs, scheme, [end], **settings)[end]
