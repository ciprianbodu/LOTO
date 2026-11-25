"""Causal inclusion models for the Romanian scoring registry."""

from __future__ import annotations

from dataclasses import dataclass
from threading import local

import numpy as np
from scipy.special import expit, logsumexp
from scipy.stats import rankdata


BOCPD_VERSION = "bayes-runlength-v1"
STACK_VERSION = "temporal-stack-v1"
HAZARD = 1.0 / 52
RUN_LIMIT = 104
PRIOR_DRAWS = 200.0
TRAINING_DAYS = 128
MIN_TRAINING_DAYS = 32
RIDGE = 12.0
_CHECKPOINTS = local()


def _draw_days(draws, universe, cutoffs):
    """Return counts per number and number of draws for each complete day."""
    matrix = np.asarray(draws)
    if matrix.ndim != 2 or not 1 <= matrix.shape[1] <= universe:
        raise ValueError("Expected a matrix with one column per drawn ball.")
    if (
        matrix.dtype.kind not in "iu"
        or np.any((matrix < 1) | (matrix > universe))
        or np.any(np.diff(np.sort(matrix, axis=1), axis=1) == 0)
    ):
        raise ValueError("Draws must contain distinct integers in the game universe.")

    starts = np.arange(len(matrix))
    if cutoffs is not None:
        bounds = np.asarray(cutoffs)
        if bounds.shape != starts.shape or (len(bounds) and bounds.dtype.kind not in "iu"):
            raise ValueError("Expected one integer day boundary per draw.")
        expected = np.maximum.accumulate(np.where(bounds == starts, starts, -1))
        if not np.array_equal(bounds, expected) or (len(bounds) and bounds[0] != 0):
            raise ValueError("Boundaries must identify consecutive complete draw days.")
        starts = starts[bounds == starts]

    sizes = np.diff(np.append(starts, len(matrix)))
    counts = np.zeros((len(starts), universe), dtype=np.int64)
    day_index = np.repeat(np.arange(len(starts)), sizes)
    np.add.at(counts, (day_index[:, None], matrix - 1), 1)
    return counts, sizes, matrix.shape[1]


def _log_evidence(alpha, beta, observed, draws):
    """Product of beta-binomial marginal likelihoods, up to a common factor."""
    evidence = np.zeros(alpha.shape[0])
    total = alpha + beta
    for i in range(draws):
        evidence += (
            np.where(observed > i, np.log(alpha + i), 0)
            + np.where(draws - observed > i, np.log(beta + i), 0)
            - np.log(total + i)
        ).sum(axis=1)
    return evidence


@dataclass
class RunLengthPosterior:
    """State over run lengths 1…104, with a reset before the next completed day."""

    fair: float
    universe: int

    def __post_init__(self):
        self.prior_alpha = PRIOR_DRAWS * self.fair
        self.prior_beta = PRIOR_DRAWS * (1 - self.fair)
        self.observed = np.zeros((0, self.universe), dtype=np.int64)
        self.draws = np.empty(0, dtype=np.int64)
        self.weights = np.empty(0)

    def observe(self, counts, draws):
        keep = min(len(self.weights), RUN_LIMIT - 1)
        growth = (1 - HAZARD) * self.weights[:keep]
        prior_weight = 1 - float(growth.sum())
        masses = np.r_[prior_weight, growth]
        history = np.vstack((np.zeros((1, self.universe), dtype=np.int64), self.observed[:keep]))
        samples = np.r_[0, self.draws[:keep]]
        alpha = self.prior_alpha + history
        beta = self.prior_beta + samples[:, None] - history
        likelihood = _log_evidence(alpha, beta, counts, int(draws))
        weights = np.log(masses, out=np.full_like(masses, -np.inf), where=masses > 0) + likelihood
        self.weights = np.exp(weights - logsumexp(weights))
        self.observed = history + counts
        self.draws = samples + draws

    def predict(self):
        keep = min(len(self.weights), RUN_LIMIT - 1)
        growth = (1 - HAZARD) * self.weights[:keep]
        predictions = (self.prior_alpha + self.observed[:keep]) / (PRIOR_DRAWS + self.draws[:keep, None])
        return (1 - growth.sum()) * self.fair + growth @ predictions


def _runlength_model(counts, sizes, draw_n):
    """Extend a process-local checkpoint only after checking its full prefix."""
    universe = counts.shape[1]
    key = universe, draw_n
    checkpoints = getattr(_CHECKPOINTS, "runlength", None)
    if checkpoints is None:
        checkpoints = _CHECKPOINTS.runlength = {}
    checkpoint = checkpoints.get(key)
    if checkpoint is not None:
        old_counts, old_sizes, model = checkpoint
        origin = len(old_sizes)
        if not (
            len(sizes) >= origin and np.array_equal(old_counts, counts[:origin])
            and np.array_equal(old_sizes, sizes[:origin])
        ):
            checkpoint = None
    if checkpoint is None:
        model = RunLengthPosterior(draw_n / universe, universe)
        origin = 0
    for counts_on_day, draws_on_day in zip(counts[origin:], sizes[origin:], strict=True):
        model.observe(counts_on_day, draws_on_day)
    if key not in checkpoints and len(checkpoints) >= 8:
        checkpoints.pop(next(iter(checkpoints)))
    checkpoints[key] = counts, sizes, model
    return model


def score_bayes_runlength_shrink(draws, max_num, *, history_cutoffs=None):
    """Rank shrinkage estimates under a truncated geometric BOCPD posterior."""
    counts, sizes, draw_n = _draw_days(draws, max_num, history_cutoffs)
    if draw_n == max_num:
        return dict.fromkeys(range(1, max_num + 1), 1.0)
    return dict(enumerate(_runlength_model(counts, sizes, draw_n).predict().tolist(), start=1))


def _before_window(values, width):
    total = np.vstack((np.zeros_like(values[:1]), np.cumsum(values, axis=0)))
    left = np.maximum(0, np.arange(len(total)) - width)
    return total - total[left]


def _stack_features(counts, sizes, draw_n):
    """Generate each historical feature vector out of fold from earlier days."""
    universe = counts.shape[1]
    fair = draw_n / universe
    rates = counts / sizes[:, None]
    next_origin = len(rates)
    origins = np.arange(next_origin + 1)
    # Training examples and the live prediction run the same feature calculation.
    expected = [
        (2.0 + _before_window(rates, width)) / (2.0 / fair + np.minimum(origins, width)[:, None])
        for width in (24, 104, 384)
    ]
    ranks = [(rankdata(probs, method="average", axis=1) - (universe + 1) / 2) * (2 / universe)
             for probs in expected]
    disagreement = np.std(ranks, axis=0)
    disagreement -= disagreement.mean(axis=1, keepdims=True)
    past_errors = _before_window(rates - expected[1][:-1], 52) / np.maximum(1, np.minimum(origins, 52))[:, None]
    return np.stack((*ranks, disagreement, 4 * past_errors), axis=-1)


def _coherent_probabilities(logits, draw_n):
    """Share a log-odds intercept so each vector has the physical draw mass."""
    lower = -40 - np.max(logits, axis=-1, keepdims=True)
    upper = 40 - np.min(logits, axis=-1, keepdims=True)
    for _ in range(38):
        guess = (lower + upper) / 2
        wide = expit(logits + guess).sum(axis=-1, keepdims=True) < draw_n
        lower = np.where(wide, guess, lower)
        upper = np.where(wide, upper, guess)
    return expit(logits + (lower + upper) / 2)


def _fit_stack(features, actual, draw_n):
    """Fit regularized logistic slopes with a coherent intercept for each day."""
    n_features = features.shape[-1]
    coefficients = np.zeros(n_features)
    prior = np.eye(n_features) * RIDGE
    for _ in range(6):
        predicted = _coherent_probabilities(features @ coefficients, draw_n)
        gradient = np.einsum("dnf,dn->f", features, actual - predicted) - RIDGE * coefficients
        variance = predicted * (1 - predicted)
        means = (features * variance[..., None]).sum(axis=1, keepdims=True) / variance.sum(axis=1)[:, None, None]
        centered = features - means
        hessian = np.einsum("dnf,dn,dng->fg", centered, variance, centered) + prior
        update = np.linalg.solve(hessian, gradient)
        coefficients += update
        if np.max(np.abs(update)) < 1e-7:
            break
    return coefficients


def score_temporal_stacked_calibration(draws, max_num, *, history_cutoffs=None):
    """Predict coherent inclusion margins from a chronological OOF rank stack."""
    counts, sizes, draw_n = _draw_days(draws, max_num, history_cutoffs)
    if draw_n == max_num or len(sizes) <= MIN_TRAINING_DAYS:
        return dict.fromkeys(range(1, max_num + 1), draw_n / max_num)
    # Only the preceding feature and fitting windows affect the prediction.
    width = TRAINING_DAYS + 384 + 52
    counts, sizes = counts[-width:], sizes[-width:]
    features = _stack_features(counts, sizes, draw_n)
    first = max(MIN_TRAINING_DAYS, len(sizes) - TRAINING_DAYS)
    actual = counts[first:] / sizes[first:, None]
    coefficients = _fit_stack(features[first:-1], actual, draw_n)
    probabilities = _coherent_probabilities(features[-1] @ coefficients, draw_n)
    return dict(enumerate(probabilities.tolist(), start=1))


def calibration_losses(probabilities, drawn):
    """Mean marginal Brier/log losses for one observed draw; smaller is better."""
    probabilities = np.asarray(probabilities, dtype=float)
    actual = np.isin(np.arange(1, len(probabilities) + 1), drawn)
    safe = np.clip(probabilities, 1e-15, 1 - 1e-15)
    brier = np.mean((probabilities - actual) ** 2)
    logloss = -np.mean(np.where(actual, np.log(safe), np.log1p(-safe)))
    return {"brier": float(brier), "log_loss": float(logloss)}


score_bayes_runlength_shrink._uses_history_cutoffs = True
score_temporal_stacked_calibration._uses_history_cutoffs = True
