"""Cele 1.000 de configurații pentru selecția dinamică de pool, 4+/5+/6+.

Coloana j corespunde numărului j+1. Fiecare scor la momentul t se bazează
exclusiv pe rândurile [:t]. Apelantul controlează gruparea pe date.
"""

from __future__ import annotations

import itertools
import math

import numpy as np
from scipy.stats import rankdata

from . import dynamic_trajectories as trajectories


def _recent_contrast(history: np.ndarray) -> np.ndarray:
    """Diferență între frecvența în ultimele 24 poziții și precedentele 96."""
    n, v = history.shape
    t = np.arange(n)
    near = np.maximum(0, t - 24)
    far = np.maximum(0, t - 120)
    prefix = np.vstack([np.zeros(v), np.cumsum(history, axis=0)])
    recent = (prefix[t] - prefix[near]) / np.maximum(1, t - near)[:, None]
    previous = (prefix[near] - prefix[far]) / np.maximum(1, near - far)[:, None]
    return recent - previous


def _gap_posterior(history: np.ndarray) -> np.ndarray:
    """Hazard discret pe număr, cu regularizare prin rata grupei de vechime."""
    v = history.shape[1]
    # Grupe mai late pentru distanțele rar observate. Vechimea zero înseamnă
    # că numărul a apărut la extragerea precedentă.
    boundaries = np.array([1, 2, 4, 8, 16, 32])
    exposed = np.zeros((len(boundaries) + 1, v))
    successes = np.zeros_like(exposed)
    age = np.zeros(v, dtype=int)
    idx = np.arange(v)
    out = np.empty_like(history)
    for t, row in enumerate(history):
        group = np.searchsorted(boundaries, age, side="right")
        pooled = (successes.sum(axis=1) + 1) / (exposed.sum(axis=1) + 2)
        out[t] = (successes[group, idx] + 20 * pooled[group]) / (
            exposed[group, idx] + 20
        )
        exposed[group, idx] += 1
        successes[group, idx] += row
        age = np.where(row, 0, age + 1)
    return out


def _conditional_markov(history: np.ndarray) -> np.ndarray:
    """Tranziții număr→număr cu uitare și prior din frecvențele marginale."""
    n, v = history.shape
    out = np.zeros((n, v))
    counts = np.zeros((v, v))
    exposures = np.zeros(v)
    marginal = np.zeros(v)
    last = np.zeros(v, dtype=bool)
    decay = 2.0 ** (-1 / 120)
    for t, row in enumerate(history):
        prior = (marginal + 0.5) / (t + 1)
        if last.any():
            conditional = (counts[last] + 20 * prior) / (exposures[last, None] + 20)
            out[t] = conditional.mean(axis=0)
        else:
            out[t] = prior
        counts *= decay
        exposures *= decay
        counts[last] += row
        exposures[last] += 1
        marginal += row
        last = row.astype(bool)
    return out


def _context_kernel(history: np.ndarray) -> np.ndarray:
    """Ponderează succesorii contextelor similare din ultimele 256 de tranziții."""
    n, v = history.shape
    out = np.zeros((n, v))
    for t in range(2, n):
        start = max(0, t - 257)
        last = history[t - 1]
        previous = history[start : t - 1]
        shared = previous @ last
        union = previous.sum(axis=1) + last.sum() - shared
        similarity = shared / np.maximum(1, union)
        weights = np.exp(8 * similarity)
        successors = history[start + 1 : t]
        out[t] = (weights @ successors) / weights.sum()
    return out


def _logistic_stack(ranks: np.ndarray, history: np.ndarray) -> np.ndarray:
    """Regresie logistică online pe ranguri, cu un gradient după fiecare rezultat."""
    n, _, v = ranks.shape
    weights = np.zeros(ranks.shape[1])
    intercept = 0.0
    out = np.empty_like(history)
    for t in range(n):
        features = (ranks[t].astype(float) - (v + 1) / 2) / v
        logits = intercept + weights @ features
        out[t] = logits
        residual = history[t] - 1.0 / (1.0 + np.exp(-logits))
        weights += 0.3 * ((features @ residual) / v - 0.01 * weights)
        intercept += 0.3 * residual.mean()
    return out


_BASE_SIGNALS = {
    "frequency_cumulative": trajectories.traj_freq_laplace,
    "ewma_10": lambda h: trajectories.traj_ewma(h, 10),
    "ewma_120": lambda h: trajectories.traj_ewma(h, 120),
    "trend_24_96": _recent_contrast,
    "gap_age": trajectories.traj_gap_hazard,
    "gap_z": trajectories.traj_gap_zscore,
    "gap_bayes": _gap_posterior,
    "weibull_085": lambda h: trajectories.traj_weibull_hazard(h, 0.85),
    "phase_2": lambda h: trajectories.traj_season_phase(h, 2),
    "phase_3": lambda h: trajectories.traj_season_phase(h, 3),
    "markov_120": _conditional_markov,
    "context_kernel_256": _context_kernel,
    "holt_damped": lambda h: trajectories.traj_holt(h, 0.1, 0.15, 0.85),
    "cusum_6": lambda h: trajectories.traj_cusum(h, 6),
    "block_entropy_100": lambda h: trajectories.traj_entropy_penalty(h, 100),
}
SIGNAL_NAMES = (*_BASE_SIGNALS, "logistic_online")


def _midranks(scores: np.ndarray) -> np.ndarray:
    # Elimină zgomotul numeric care ar rupe egalitățile dintre două coloane.
    return rankdata(np.round(scores, 12), axis=1, method="average")


def signal_ranks(history: np.ndarray, universe: int) -> tuple[list[str], np.ndarray]:
    """Întoarce numele și rangurile (rând, semnal, număr), de la 1 la V.

    Rangurile la egalitate sunt medii, exacte în float32. Selecția finală
    a celor K numere și departajarea lor canonică revin apelantului.
    """
    history = np.asarray(history, dtype=np.float64)
    if history.ndim != 2 or universe < 1 or history.shape[1] != universe:
        raise ValueError("Istoricul trebuie să fie o matrice cu V coloane.")
    if not np.all((history == 0) | (history == 1)):
        raise ValueError("Istoricul trebuie să conțină numai indicatori 0/1.")
    ranks = np.empty((len(history), len(SIGNAL_NAMES), universe), dtype=np.float32)
    if len(history):
        for j, predict in enumerate(_BASE_SIGNALS.values()):
            ranks[:, j] = _midranks(predict(history))
        ranks[:, -1] = _midranks(_logistic_stack(ranks[:, :-1], history))
    return list(SIGNAL_NAMES), ranks


RECIPE_SEED = 20261119


def _make_recipes():
    """32 single-signal directions, 360 pair blends, 304 triples, 304 quintuples."""
    recipes = []
    for i in range(len(SIGNAL_NAMES)):
        recipes.extend([((i, 1),), ((i, -1),)])
    for i, j in itertools.combinations(range(len(SIGNAL_NAMES)), 2):
        recipes.extend([((i, a), (j, b)) for a, b in ((1, 1), (2, 1), (1, 2))])
    rng = np.random.default_rng(RECIPE_SEED)
    for size in (3, 5):
        seen = set()
        while len(seen) < 304:
            indices = sorted(
                rng.choice(len(SIGNAL_NAMES), size, replace=False).tolist()
            )
            weights = rng.integers(1, 5, size=size).tolist()
            if rng.random() < 0.5:
                weights[rng.integers(size)] *= -1
            divisor = math.gcd(*weights)
            recipe = tuple(
                (i, w // divisor) for i, w in zip(indices, weights, strict=True)
            )
            if recipe not in seen:
                recipes.append(recipe)
                seen.add(recipe)
    if len(set(recipes)) != 1000:
        raise ValueError("The frozen grid must contain exactly 1,000 distinct recipes")
    return {f"C{i:04d}": recipe for i, recipe in enumerate(recipes, 1)}


_RECIPES = _make_recipes()
RECIPE_IDS = tuple(_RECIPES)


def configurations() -> dict[str, tuple[tuple[int, int], ...]]:
    """Ponderile nenule pentru fiecare ID, în ordinea din studiul istoric."""
    return _RECIPES.copy()


def _weight_matrix() -> np.ndarray:
    table = np.zeros((len(_RECIPES), len(SIGNAL_NAMES)), dtype=np.int8)
    for row, recipe in enumerate(_RECIPES.values()):
        for signal, weight in recipe:
            table[row, signal] = weight
    table.setflags(write=False)
    return table


RECIPE_WEIGHTS = _weight_matrix()


def recipe_scores(ranks_for_one_origin: np.ndarray) -> np.ndarray:
    """Scor brut pentru fiecare configurație și număr la un singur pas.

    Rangurile medii sunt întregi sau semiîntregi. Cu aceste ponderi mici,
    suma ponderată este exactă în float32, inclusiv la scoruri egale.
    Apelantul aplică normalizarea, penalizările și rezerva pentru scorul plat.
    """
    ranks = np.asarray(ranks_for_one_origin, dtype=np.float32)
    if ranks.ndim != 2 or ranks.shape[0] != len(SIGNAL_NAMES) or not ranks.shape[1]:
        raise ValueError("Este necesară o matrice de ranguri semnale × numere.")
    if not np.isfinite(ranks).all():
        raise ValueError("Matricea de ranguri conține valori nefinite.")
    return RECIPE_WEIGHTS @ ranks
