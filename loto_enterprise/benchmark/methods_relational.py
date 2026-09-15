"""Metode de scoring relaționale (10 metode).

Ipoteze despre legăturile dintre numere și despre forma extragerii:

    markov_pairs / markov_lag2      tranziții extragere → extragerea următoare (lag 1 / lag 2)
    naive_bayes_last                dovezile ultimei extrageri combinate multiplicativ (Naive Bayes)
    markov_self_state               lanț cu două stări per număr: „a ieșit / n-a ieșit" data trecută
    cooc_last3 / anti_cooc_last     afinitate de co-apariție cu ultimele extrageri / contrariul
    pair_lift_last                  lift-ul perechilor față de independență, spre ultima extragere
    pagerank_cooc                   centralitate PageRank în graful de co-apariție (ponderat recent)
    knn_draw_similarity             ce a urmat după extragerile cele mai asemănătoare cu ultima
    neighbor_adjacent               vecinii numerici (±1, ±2) ai ultimei extrageri
                                    (filtru spațial: top-K = clasa de vecinătate;
                                    rămâne în bench, EXCLUDED_FROM_PRODUCTION)

`neighbor_adjacent` e filtru de poziție pe axa 1…N, nu predictor. Paritate /
sume / decade au fost scoase înainte de bench (14.09.2026); acesta a rămas
și a fost exclus din producție la auditul din 15.09.2026.

Pe geometria cu o singură bilă (Joker Urna 2) co-aparițiile din aceeași
extragere nu există: metodele bazate pe ele dau scoruri plate, iar bench-ul
le marchează ca inutilizabile acolo, nu le maschează.
"""

from __future__ import annotations

import numpy as np

from .methods_common import (
    ewma_weights,
    indicator,
    make_registry,
    vector_to_scores,
)

_EPS = 1e-9


def _freq_tiebreak(ind: np.ndarray, window: int = 300) -> np.ndarray:
    """Termen mic (×0.001) care sparge egalitățile prin frecvența recentă."""
    if ind.shape[0] == 0:
        return np.zeros(ind.shape[1])
    return 0.001 * ind[-window:].mean(axis=0)


def _transition_counts(ind: np.ndarray, lag: int) -> tuple[np.ndarray, np.ndarray]:
    """(T, count): T[i, j] = de câte ori i la t și j la t+lag; count[i] = aparițiile lui i ca sursă."""
    n = ind.shape[0]
    if n <= lag:
        m = ind.shape[1]
        return np.zeros((m, m)), np.zeros(m)
    src = ind[:-lag]
    dst = ind[lag:]
    return src.T @ dst, src.sum(axis=0)


def _cooc(ind: np.ndarray, weights: np.ndarray | None = None) -> np.ndarray:
    if weights is None:
        c = ind.T @ ind
    else:
        c = ind.T @ (weights[:, None] * ind)
    np.fill_diagonal(c, 0.0)
    return c


# --------------------------------------------------------------------------- #
# Tranziții
# --------------------------------------------------------------------------- #
def _markov(draws_2d, max_num, lag: int):
    ind = indicator(draws_2d, max_num)
    n, m = ind.shape
    if n <= lag:
        return vector_to_scores(np.zeros(m), max_num)
    T, count = _transition_counts(ind, lag)
    P = T / np.maximum(count, 1.0)[:, None]
    src_draw = ind[n - lag]
    return vector_to_scores(src_draw @ P, max_num)


def score_markov_pairs(draws_2d, max_num):
    return _markov(draws_2d, max_num, lag=1)


def score_markov_lag2(draws_2d, max_num):
    """Tranziții de la extragerea de acum două pași (dependență cu lag 2)."""
    return _markov(draws_2d, max_num, lag=2)


def score_naive_bayes_last(draws_2d, max_num):
    """Naive Bayes: Σ_{i∈ultima} log P(j la t+1 | i la t) − (k−1)·log P(j), Laplace.

    Spre deosebire de `markov_pairs` (sumă de probabilități condiționate),
    combină multiplicativ dovezile din numerele ultimei extrageri.
    """
    ind = indicator(draws_2d, max_num)
    n, m = ind.shape
    if n < 2:
        return vector_to_scores(np.zeros(m), max_num)
    T, count = _transition_counts(ind, 1)
    p_cond = (T + 1.0) / (count[:, None] + 2.0)  # P(j la t+1 | i la t)
    p_j = (ind[1:].sum(axis=0) + 1.0) / (n - 1 + 2.0)  # P(j la t+1)
    last = ind[-1]
    k = float(last.sum())
    if k == 0:
        return vector_to_scores(np.log(p_j), max_num)
    log_odds = last @ np.log(p_cond) - (k - 1.0) * np.log(p_j)
    return vector_to_scores(log_odds, max_num)


def score_markov_self_state(draws_2d, max_num):
    """P(iese la t+1 | a ieșit la t) sau P(iese la t+1 | n-a ieșit la t), per număr, Laplace."""
    ind = indicator(draws_2d, max_num)
    n, m = ind.shape
    if n < 2:
        return vector_to_scores(np.zeros(m), max_num)
    prev, nxt = ind[:-1], ind[1:]
    n11 = (prev * nxt).sum(axis=0)
    n1 = prev.sum(axis=0)
    n01 = ((1.0 - prev) * nxt).sum(axis=0)
    n0 = (1.0 - prev).sum(axis=0)
    p11 = (n11 + 1.0) / (n1 + 2.0)
    p01 = (n01 + 1.0) / (n0 + 2.0)
    last = ind[-1]
    return vector_to_scores(np.where(last > 0, p11, p01), max_num)


# --------------------------------------------------------------------------- #
# Co-apariție
# --------------------------------------------------------------------------- #
def score_cooc_last3(draws_2d, max_num):
    ind = indicator(draws_2d, max_num)
    n, m = ind.shape
    if n == 0:
        return vector_to_scores(np.zeros(m), max_num)
    c = _cooc(ind)
    recent = ind[-3:].sum(axis=0)
    return vector_to_scores(recent @ c, max_num)


def score_anti_cooc_last(draws_2d, max_num):
    ind = indicator(draws_2d, max_num)
    n, m = ind.shape
    if n == 0:
        return vector_to_scores(np.zeros(m), max_num)
    c = _cooc(ind)
    count = np.maximum(ind.sum(axis=0), 1.0)
    return vector_to_scores(-(ind[-1] @ (c / count[:, None])), max_num)


def score_pair_lift_last(draws_2d, max_num):
    """lift(i, j) = n·C[i,j] / (c_i·c_j), mediat peste i din ultima extragere."""
    ind = indicator(draws_2d, max_num)
    n, m = ind.shape
    if n == 0:
        return vector_to_scores(np.zeros(m), max_num)
    c = _cooc(ind)
    count = ind.sum(axis=0)
    lift = (n * c + 1.0) / (np.outer(count, count) + 1.0)
    np.fill_diagonal(lift, 0.0)
    last = ind[-1]
    k = max(float(last.sum()), 1.0)
    return vector_to_scores((last @ lift) / k, max_num)


def score_pagerank_cooc(draws_2d, max_num, damping: float = 0.85, iters: int = 60):
    ind = indicator(draws_2d, max_num)
    n, m = ind.shape
    if n == 0:
        return vector_to_scores(np.zeros(m), max_num)
    c = _cooc(ind, ewma_weights(n, 200.0))
    col_sum = c.sum(axis=0)
    if col_sum.sum() <= 0:
        return vector_to_scores(np.zeros(m), max_num)
    M = c / np.maximum(col_sum, _EPS)[None, :]
    dangling = col_sum <= 0
    r = np.full(m, 1.0 / m)
    for _ in range(iters):
        r = damping * (M @ r + r[dangling].sum() / m) + (1.0 - damping) / m
    return vector_to_scores(r, max_num)


# --------------------------------------------------------------------------- #
# Vecinătăți și similaritate
# --------------------------------------------------------------------------- #
def score_knn_draw_similarity(draws_2d, max_num, k: int = 40):
    """Extragerile trecute cele mai asemănătoare cu ultima (suprapunere), ce a urmat după ele."""
    ind = indicator(draws_2d, max_num)
    n, m = ind.shape
    if n < 3:
        return vector_to_scores(np.zeros(m), max_num)
    past, follow = ind[:-1], ind[1:]
    sims = past @ ind[-1]
    # cele mai similare; la egalitate, cele mai recente (argsort stabil pe -sim, apoi -t)
    order = np.lexsort((-np.arange(past.shape[0]), -sims))[: min(k, past.shape[0])]
    w = sims[order] + 1.0
    return vector_to_scores(w @ follow[order], max_num)


def score_neighbor_adjacent(draws_2d, max_num):
    ind = indicator(draws_2d, max_num)
    n, m = ind.shape
    if n == 0:
        return vector_to_scores(np.zeros(m), max_num)
    last = ind[-1]
    out = np.zeros(m)
    out[1:] += last[:-1]
    out[:-1] += last[1:]
    out[2:] += 0.5 * last[:-2]
    out[:-2] += 0.5 * last[2:]
    return vector_to_scores(out + _freq_tiebreak(ind), max_num)


RELATIONAL_METHODS = make_registry(
    [
        ("markov_pairs", score_markov_pairs, "transition", "tranziții extragere → următoarea, lag 1"),
        ("markov_lag2", score_markov_lag2, "transition", "tranziții cu lag 2"),
        ("naive_bayes_last", score_naive_bayes_last, "transition", "Naive Bayes multiplicativ pe ultima extragere"),
        ("markov_self_state", score_markov_self_state, "transition", "lanț cu două stări per număr"),
        ("cooc_last3", score_cooc_last3, "cooccurrence", "co-apariție cu ultimele 3 extrageri"),
        ("anti_cooc_last", score_anti_cooc_last, "cooccurrence", "contrariul co-apariției cu ultima extragere"),
        ("pair_lift_last", score_pair_lift_last, "cooccurrence", "lift-ul perechilor spre ultima extragere"),
        ("pagerank_cooc", score_pagerank_cooc, "graph", "PageRank pe graful de co-apariție ponderat recent"),
        ("knn_draw_similarity", score_knn_draw_similarity, "similarity", "ce a urmat după cele 40 de extrageri cele mai asemănătoare"),
        ("neighbor_adjacent", score_neighbor_adjacent, "structure", "filtru spațial: vecinii ±1/±2 ai ultimei extrageri (exclus din producție)"),
    ]
)

__all__ = ["RELATIONAL_METHODS"]
