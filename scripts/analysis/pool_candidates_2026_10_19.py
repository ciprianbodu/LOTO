"""Four preregistered causal expert aggregators for Loto 6/49, 2026-10-19.

Research module only: never imported by the application or registered in METHODS.
No history, benchmark decision or external service is read here. Parameters were
fixed before evaluating the Romanian/external outcomes for this experiment.

New relative to the 2026-10-05/09/10 screens: soft exponential expert aggregation
trained on the *actual* 4+ event with the user's consecutive-number limit, rather
than static rank ensembles, per-ball regression or a hard follow-the-leader rule.
This is a hypothesis to evaluate; averaging expert scores has no guarantee of
beating an expert, and 4+ is a rare and noisy online reward.

Contract
--------
``candidate_scores(expert_scores, draws, expert_names, *, groups=None,
                   frequency_past=None) -> dict[str, ndarray[T,49]]``

* expert_scores: time-first float array (T,M,49), where each expert row has
  already been computed using only draws with date strictly before that row.
* draws: integer ball labels (T,6), or an equivalent binary indicator (T,49).
* expert_names: M distinct names, including the anchor ``markov_lag2``.
* groups: T chronological day/group labels. Equal labels must be contiguous.
  Without groups every row is a separate observation. All predictions for a
  group are emitted before *any* target in that group changes the weights.
* frequency_past: optional (T,49) finite nonnegative CANONICAL frequency
  scores computed from the full strict prefix (including the application's
  exponential decay/min-max normalization), used ONLY to replace flat/non-finite
  expert rows. It is not an extra tie-break. If omitted, a convenience fallback
  uses empirical counts of previous groups of the supplied draws; those counts
  are not a substitute for the canonical full-prefix frequency scorer. The main
  experiment supplies the canonical scores, including its pre-warmup history.

Each expert receives, after a draw, reward mean_K 1{pool_K hits >= 4}, for
K=9..16 and ``max_consecutive_run=2``. The pools use the application's canonical
score/number tie-break and exact consecutive-limit helper. Each reward is in
[0,1]; same-day rewards are summed only after all that day's predictions.

Four outputs: weighted_experts_h100_rank / _vote and h400_rank / _vote. Both
representations share expert weights. Priors are 0.5 for markov_lag2 and the
remaining 0.5 uniformly across other experts. For half-life h and a group of b
observations, discount the old log weight/prior ratios by 2**(-b/h), add the
sum of the b rewards (eta=1), normalize, then mix toward the prior with share
1-(1-0.01)**b. This batched convention is independent of within-day row order.

Rank features are exact mean ranks scaled to [0,1], without numerical jitter
or label-based adjustment of score ties. Vote features average membership in
that expert's eight constrained pools. The numeric consecutive constraint is
part of the user's objective, so these vote/reward features are not claimed to
be invariant under arbitrary relabelling of the lottery balls.

``generate(scores, actuals, frequency_past, names, groups=...)`` provides an
iterator of (name, family, scores[T,49]) for the experiment driver's bank API.
These arrays are prequential: output at t depends on expert/frequency rows at t
and only outcomes from strictly earlier groups. Inputs are never mutated.

Verify: python -m pytest -q test_pool_candidates_2026_10_19.py
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from functools import lru_cache
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from loto_enterprise.core.ranking import limit_consecutive_run, rank_by_score
from loto_enterprise.core.score_validation import SCORE_VARIANCE_EPSILON

POOL_SIZES = tuple(range(9, 17))
HALF_LIVES = (100, 400)
ETA = 1.0
FIXED_SHARE = 0.01
ANCHOR = "markov_lag2"
FAMILY = "online_experts_constrained_4plus"
CANDIDATE_NAMES = tuple(
    f"weighted_experts_h{half_life}_{representation}"
    for half_life in HALF_LIVES
    for representation in ("rank", "vote")
)


def _actuals(draws: np.ndarray, rows: int) -> np.ndarray:
    values = np.asarray(draws)
    if values.shape == (rows, 49):
        if not np.all((values == 0) | (values == 1)) or not np.all(
            values.sum(axis=1) == 6
        ):
            raise ValueError("Actual indicators must contain exactly six ones per row")
        return values.astype(np.uint8, copy=True)
    if values.shape != (rows, 6):
        raise ValueError("draws must have shape (T,6) or (T,49)")
    try:
        numeric = np.asarray(values, dtype=np.float64)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Draw labels must be six distinct integers in 1..49") from exc
    if not np.isfinite(numeric).all() or not np.equal(numeric, np.floor(numeric)).all():
        raise ValueError("Draw labels must be six distinct integers in 1..49")
    if np.any((numeric < 1) | (numeric > 49)) or any(
        len(set(row)) != 6 for row in numeric
    ):
        raise ValueError("Draw labels must be six distinct integers in 1..49")
    out = np.zeros((rows, 49), dtype=np.uint8)
    if rows:
        out[np.arange(rows)[:, None], numeric.astype(np.int64) - 1] = 1
    return out


def _group_slices(groups, rows: int) -> list[tuple[int, int]]:
    if groups is None:
        return [(t, t + 1) for t in range(rows)]
    labels = np.asarray(groups)
    if labels.shape != (rows,):
        raise ValueError("groups must have shape (T,)")
    if not rows:
        return []
    out = []
    seen = set()
    start = 0
    for stop in range(1, rows + 1):
        if stop < rows and labels[stop] == labels[start]:
            continue
        label = labels[start]
        try:
            if label != label or label in seen:
                raise ValueError("Group labels must be non-missing and contiguous")
            seen.add(label)
        except TypeError as exc:
            raise ValueError("Group labels must be scalar and hashable") from exc
        out.append((start, stop))
        start = stop
    return out


def _prefix_frequency(actuals: np.ndarray, group_slices) -> np.ndarray:
    out = np.empty(actuals.shape, dtype=np.float64)
    running = np.zeros(49, dtype=np.float64)
    for start, stop in group_slices:
        out[start:stop] = running
        running += actuals[start:stop].sum(axis=0)
    return out


def _prior(expert_names: Sequence[str]) -> np.ndarray:
    if (
        not expert_names
        or len(set(expert_names)) != len(expert_names)
        or ANCHOR not in expert_names
    ):
        raise ValueError("Distinct expert names must include markov_lag2")
    if len(expert_names) == 1:
        return np.ones(1, dtype=np.float64)
    prior = np.full(len(expert_names), 0.5 / (len(expert_names) - 1), dtype=np.float64)
    prior[list(expert_names).index(ANCHOR)] = 0.5
    return prior


def _usable_rows(scores: np.ndarray, frequency: np.ndarray) -> np.ndarray:
    with np.errstate(over="ignore", invalid="ignore"):
        usable = np.isfinite(scores).all(axis=1) & (
            np.ptp(scores, axis=1) > SCORE_VARIANCE_EPSILON
        )
    clean = scores.copy()
    clean[~usable] = frequency
    return clean


def _mean_ranks(scores: np.ndarray) -> np.ndarray:
    """Exact mean ranks; ties have the same feature before canonical top-K."""
    out = np.empty(scores.shape, dtype=np.float64)
    for m, row in enumerate(scores):
        order = np.argsort(row, kind="stable")
        ordered = row[order]
        starts = np.r_[0, np.flatnonzero(ordered[1:] != ordered[:-1]) + 1]
        stops = np.r_[starts[1:], len(row)]
        for start, stop in zip(starts, stops):
            # Zero-based average rank divided by 48 gives [0,1].
            out[m, order[start:stop]] = (int(start) + int(stop) - 1) / 96.0
    return out


@lru_cache(maxsize=4096)
def _pool_masks(ranked: tuple[int, ...]) -> np.ndarray:
    masks = np.zeros((len(POOL_SIZES), 49), dtype=np.uint8)
    for index, size in enumerate(POOL_SIZES):
        pool, applied, _skipped = limit_consecutive_run(list(ranked), size, 2)
        if applied != 2 or len(pool) != size:
            raise RuntimeError(
                "Full 49-ball universe must fit pools 9..16 with max run 2"
            )
        masks[index, np.asarray(pool) - 1] = 1
    masks.setflags(write=False)
    return masks


def _features(scores: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    masks = np.stack(
        [
            _pool_masks(tuple(rank_by_score(dict(enumerate(row, start=1)), 49)))
            for row in scores
        ]
    )
    return _mean_ranks(scores), masks.mean(axis=1), masks


def _update(
    weights: np.ndarray,
    prior: np.ndarray,
    reward_sum: np.ndarray,
    observations: int,
    half_life: int,
) -> np.ndarray:
    decay = 2.0 ** (-observations / half_life)
    logits = np.log(prior) + decay * np.log(weights / prior) + ETA * reward_sum
    tilted = np.exp(logits - logits.max())
    tilted /= tilted.sum()
    share = 1.0 - (1.0 - FIXED_SHARE) ** observations
    return (1.0 - share) * tilted + share * prior


def candidate_scores(
    expert_scores: np.ndarray,
    draws: np.ndarray,
    expert_names: Sequence[str],
    *,
    groups=None,
    frequency_past: np.ndarray | None = None,
    cumulative_frequency_past: np.ndarray | None = None,
) -> dict[str, np.ndarray]:
    """Return the four fixed prequential score matrices; never select a winner.

    The cumulative_frequency_past keyword is a compatibility alias. Passing
    both frequency keyword arguments is rejected rather than guessing.
    """
    scores = np.asarray(expert_scores, dtype=np.float64)
    names = tuple(expert_names)
    if scores.ndim != 3 or scores.shape[1:] != (len(names), 49):
        raise ValueError("expert_scores must have shape (T,M,49)")
    prior = _prior(names)
    rows = len(scores)
    actual = _actuals(draws, rows)
    slices = _group_slices(groups, rows)
    if frequency_past is not None and cumulative_frequency_past is not None:
        raise ValueError("Pass only one frequency keyword")
    supplied_frequency = (
        frequency_past if frequency_past is not None else cumulative_frequency_past
    )
    if supplied_frequency is None:
        frequencies = _prefix_frequency(actual, slices)
    else:
        frequencies = np.asarray(supplied_frequency, dtype=np.float64)
        if (
            frequencies.shape != (rows, 49)
            or not np.isfinite(frequencies).all()
            or np.any(frequencies < 0)
        ):
            raise ValueError(
                "frequency_past must be finite nonnegative (T,49) prefix frequency scores"
            )
    out = {name: np.empty((rows, 49), dtype=np.float64) for name in CANDIDATE_NAMES}
    weights = {half_life: prior.copy() for half_life in HALF_LIVES}
    for start, stop in slices:
        rewards = np.zeros(len(names), dtype=np.float64)
        for t in range(start, stop):
            clean = _usable_rows(scores[t], frequencies[t])
            rank_features, vote_features, masks = _features(clean)
            # Every output for this row is written before reading its outcome;
            # weights stay unchanged throughout a same-date group.
            for half_life in HALF_LIVES:
                out[f"weighted_experts_h{half_life}_rank"][t] = (
                    weights[half_life] @ rank_features
                )
                out[f"weighted_experts_h{half_life}_vote"][t] = (
                    weights[half_life] @ vote_features
                )
            hits = np.einsum("mkn,n->mk", masks, actual[t], dtype=np.int64)
            rewards += (hits >= 4).mean(axis=1)
        for half_life in HALF_LIVES:
            weights[half_life] = _update(
                weights[half_life], prior, rewards, stop - start, half_life
            )
    return out


def generate(
    expert_scores, actuals, cumulative_frequency_past, expert_names, *, groups=None
) -> Iterator[tuple[str, str, np.ndarray]]:
    """Bank adapter: (candidate name, family, time-first score matrix)."""
    for name, scores in candidate_scores(
        expert_scores,
        actuals,
        expert_names,
        groups=groups,
        frequency_past=cumulative_frequency_past,
    ).items():
        yield name, FAMILY, scores
