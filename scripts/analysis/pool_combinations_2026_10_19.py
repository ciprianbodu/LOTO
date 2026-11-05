"""Outcome-free combinations of causal 6/49 expert scores, 2026-10-19.

Research only. This NEW bank does not change the earlier frozen screen, METHODS,
production selection, history, or saved decisions. It reads no lottery outcomes,
CSV, dates, hit matrices, target pool size or external service.

Contract
--------
``iter_candidates(scores[T,M,49], names, *, audit=None)`` yields
``(candidate_name, family, score[T,49])``. Larger scores are better. Input rows
must already be computed on strict chronological prefixes by the caller; the
bank is pointwise in time and never inspects another row to score the current
row. Inputs contain finite complete scores after the caller's canonical scorer
fallback. All combination ties use scipy's exact mean ranks, without ball-label
jitter. The experiment runner alone applies canonical top-K and consecutive2.

For M=47 production experts including markov_lag2, frequency and the ten fixed
semantic experts below, there are 208 combinations:
* 3*(M-1)=138 mean-rank pairs: Markov weight .25/.5/.75, every other expert;
* 2*4=8 robust group combinations: median and symmetric 20% trimmed mean;
* 3*4=12 reciprocal-rank fusions with offsets 0/10/60;
* 4 geometric means of rank positions (negative means favor smaller positions);
* M-1=46 residual corrections: Markov + .5 * the other expert's centered rank
  residual after projection onto the centered Markov rank.

The four semantic groups are fixed in GROUPS. They do not expand, get reweighted,
or get selected using performance when the input bank contains more experts.
RRF offset0 is already monotone-equivalent to harmonic rank pooling; a separate
harmonic candidate would duplicate it. Equal-weight rank pairs belong to the
static-rank ensemble family explored previously; they are controls within this
broader bank, not a claim of a newly invented algorithm.

A perfectly flat consensus (e.g. equal-weight opposite rankings) falls back
EXACTLY to the canonical frequency score already supplied in the same row.
If ``audit`` is provided, ``audit[name]`` is a bool[T] array marking these rows,
available before the corresponding yield. The orchestrator can exclude a
candidate using only its past-window fallback mask. The bank does not choose
eligibility, a finalist or a split. Frequency must itself be finite and usable.

``candidate_specs(names=None)`` returns JSON-friendly descriptors, including
parent_names, complexity (number of distinct formula parents), and explicit
parameters. The common flat guard is documented separately and is not counted
as another formula parent. With names omitted only, read the current production
registry to enumerate names; no scorer is executed. Passing names explicitly
makes both descriptions and generation independent of registry changes.

Residual coefficients use centered mean ranks in exact integer half-rank units.
Their covariance/variance sums are integers, so relabelling balls cannot perturb
the projection coefficient via floating-point reduction order. No residual
variance normalization amplifies small disagreements.

Verify: python -m pytest -q test_pool_combinations_2026_10_19.py
"""

from __future__ import annotations

from collections.abc import Iterator, MutableMapping, Sequence
from pathlib import Path
import sys

import numpy as np
from scipy.stats import rankdata

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from loto_enterprise.core.score_validation import SCORE_VARIANCE_EPSILON

ANCHOR = "markov_lag2"
FREQUENCY = "frequency"
MARKOV_WEIGHTS = (0.25, 0.5, 0.75)
RRF_OFFSETS = (0, 10, 60)
TRIM_FRACTION = 0.2
RESIDUAL_WEIGHT = 0.5
SEMANTIC_EXPERTS = (
    "frequency",
    "markov_pairs",
    "markov_lag2",
    "ewma_hl30",
    "freq_window_50",
    "freq_window_200",
    "rank_ensemble_core",
    "logit_pooled_feats",
    "ridge_pooled_feats",
    "hot_consistency",
)
GROUPS = {
    "recency": (
        "frequency",
        "ewma_hl30",
        "freq_window_50",
        "freq_window_200",
        "hot_consistency",
    ),
    "balanced": (
        "markov_lag2",
        "markov_pairs",
        "frequency",
        "freq_window_200",
        "hot_consistency",
    ),
    "learned": (
        "markov_lag2",
        "frequency",
        "rank_ensemble_core",
        "logit_pooled_feats",
        "ridge_pooled_feats",
    ),
    "all10": SEMANTIC_EXPERTS,
}


def _names(names: Sequence[str] | None) -> tuple[str, ...]:
    if names is None:
        from loto_enterprise.benchmark.decision import EXCLUDED_FROM_PRODUCTION
        from loto_enterprise.benchmark.methods import METHODS

        names = sorted(set(METHODS) - EXCLUDED_FROM_PRODUCTION)
    out = tuple(names)
    if (
        not out
        or any(not isinstance(name, str) or not name for name in out)
        or len(set(out)) != len(out)
    ):
        raise ValueError("Expert names must be distinct nonempty strings")
    missing = set(SEMANTIC_EXPERTS) - set(out)
    if missing:
        raise ValueError(f"Missing fixed semantic experts: {sorted(missing)}")
    return out


def candidate_specs(names: Sequence[str] | None = None) -> list[dict]:
    """Describe the deterministic bank without computing or selecting scores."""
    names = _names(names)
    others = sorted(set(names) - {ANCHOR})
    out = []
    for expert in others:
        for weight in MARKOV_WEIGHTS:
            out.append(
                {
                    "name": f"combo_rank_m{round(weight * 100):03d}__{expert}",
                    "family": "pair_mean_rank",
                    "rule": "weighted_mean_rank",
                    "parent_names": [ANCHOR, expert],
                    "markov_weight": weight,
                }
            )
    for group, members in GROUPS.items():
        out.append(
            {
                "name": f"combo_median__{group}",
                "family": "robust_rank",
                "rule": "median_mean_rank",
                "parent_names": list(members),
            }
        )
        out.append(
            {
                "name": f"combo_trim20__{group}",
                "family": "robust_rank",
                "rule": "trimmed_mean_rank",
                "parent_names": list(members),
                "trim_fraction": TRIM_FRACTION,
            }
        )
    for group, members in GROUPS.items():
        for offset in RRF_OFFSETS:
            out.append(
                {
                    "name": f"combo_rrf_c{offset:02d}__{group}",
                    "family": "rrf_rank",
                    "rule": "sum_reciprocal_rank",
                    "parent_names": list(members),
                    "offset": offset,
                }
            )
    for group, members in GROUPS.items():
        out.append(
            {
                "name": f"combo_geomean__{group}",
                "family": "geometric_rank",
                "rule": "negative_geometric_mean_position",
                "parent_names": list(members),
            }
        )
    for expert in others:
        out.append(
            {
                "name": f"combo_residual_a050__{expert}",
                "family": "residual_rank",
                "rule": "markov_plus_orthogonal_rank_residual",
                "parent_names": [ANCHOR, expert],
                "residual_weight": RESIDUAL_WEIGHT,
            }
        )
    for descriptor in out:
        descriptor.update(
            complexity=len(set(descriptor["parent_names"])),
            mean_rank_ties="average",
            flat_fallback=FREQUENCY,
            flat_epsilon=SCORE_VARIANCE_EPSILON,
        )
    return out


def _flat_mask(values: np.ndarray) -> np.ndarray:
    return np.ptp(values, axis=1) <= SCORE_VARIANCE_EPSILON


def _finished(
    values: np.ndarray, frequency: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    if not np.isfinite(values).all():
        raise RuntimeError("A finite rank combination produced nonfinite values")
    flat = _flat_mask(values)
    if flat.any():
        values = values.copy()
        values[flat] = frequency[flat]
    return values, flat


def iter_candidates(
    scores: np.ndarray,
    names: Sequence[str],
    *,
    audit: MutableMapping[str, np.ndarray] | None = None,
) -> Iterator[tuple[str, str, np.ndarray]]:
    """Yield fixed score combinations; audit[name] marks flat-consensus fallback.

    No method is fitted across time. Formula parents are resolved by name,
    so reordering the input method axis cannot alter a candidate's score.
    """
    names = _names(names)
    raw = np.asarray(scores, dtype=np.float64)
    if raw.ndim != 3 or raw.shape[1:] != (len(names), 49):
        raise ValueError("scores must have shape (T,M,49)")
    if not np.isfinite(raw).all():
        raise ValueError("Expert scores must be finite after canonical cache fallback")
    index = {name: j for j, name in enumerate(names)}
    frequency = raw[:, index[FREQUENCY], :]
    if _flat_mask(frequency).any():
        raise ValueError(
            "Canonical frequency fallback must itself have usable variance"
        )
    # Average ranks and rank positions are exact integer or half-integer values.
    quality = np.asarray(rankdata(raw, axis=-1, method="average"), dtype=np.float64)
    positions = 50.0 - quality
    quality -= 1.0
    quality /= 48.0
    base = quality[:, index[ANCHOR], :]
    base_units = (50 - 2 * positions[:, index[ANCHOR], :]).astype(np.int64)
    base_norm = np.einsum("tn,tn->t", base_units, base_units, dtype=np.int64)
    for spec in candidate_specs(names):
        members = [index[name] for name in spec["parent_names"]]
        rule = spec["rule"]
        if rule == "weighted_mean_rank":
            weight = spec["markov_weight"]
            value = weight * base + (1.0 - weight) * quality[:, members[1], :]
        elif rule == "median_mean_rank":
            value = np.median(quality[:, members, :], axis=1)
        elif rule == "trimmed_mean_rank":
            ordered = np.sort(quality[:, members, :], axis=1)
            trim = int(len(members) * spec["trim_fraction"])
            value = ordered[:, trim : len(members) - trim, :].mean(axis=1)
        elif rule == "sum_reciprocal_rank":
            value = np.sum(1.0 / (spec["offset"] + positions[:, members, :]), axis=1)
        elif rule == "negative_geometric_mean_position":
            value = -np.exp(np.log(positions[:, members, :]).mean(axis=1))
        elif rule == "markov_plus_orthogonal_rank_residual":
            expert_units = (50 - 2 * positions[:, members[1], :]).astype(np.int64)
            product = np.einsum("tn,tn->t", base_units, expert_units, dtype=np.int64)
            coefficient = np.divide(
                product, base_norm, out=np.zeros(len(raw)), where=base_norm != 0
            )
            residual = (expert_units - coefficient[:, None] * base_units) / 96.0
            value = base + spec["residual_weight"] * residual
        else:
            raise RuntimeError(f"Unknown fixed combination rule: {rule}")
        value, flat = _finished(value, frequency)
        if audit is not None:
            audit[spec["name"]] = flat.copy()
        yield spec["name"], spec["family"], value
