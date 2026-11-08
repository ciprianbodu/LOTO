"""Metode bayesiene online și ansamblu de experți (2 metode).

Al treilea val (2026-11-08), la cererea utilizatorului („metode inteligente
super avansate"):

- `bayes_dirichlet_forget` — estimator bayesian online Dirichlet-multinomial
  cu uitare exponențială: forma cu netezidare corectă bayesian a frecvenței
  ponderate temporal. Spre deosebire de EWMA brut, media posterioară trage
  spre uniform la istorii scurte (prior Jeffreys) și se apropie de frecvența
  recentă la istorii lungi.
- `hedge_experts_mixture` — predicție cu experți (Hedge / exponential weights
  pe log-loss, η = 1, alegerea teoretică pentru un loss 1-mixabil): șase
  predictori online în formă închisă (uniform, frecvență Laplace, EWMA rapid,
  EWMA lent, hazard de gol, tranziții din ultima extragere) ponderați după
  verosimilitatea pe care au acordat-o extragerilor deja văzute. Scorul este
  mixtura predictivelor cu ponderile finale — „universal prediction" aplicată
  peste o grădină mică de modele simple, spre deosebire de
  `rank_ensemble_core` (medie statică de ranguri, fără feedback online).

Ambele sunt IPOTEZE pentru bench, nu dovezi: intră în aceeași poartă
walk-forward + corecție Holm ca restul registry-ului și nu sunt promovate în
producție în afara deciziei existente. Deterministe, fără stare între apeluri,
cauzale (folosesc strict prefixul primit), fără dependențe în afara NumPy.
"""

from __future__ import annotations

import numpy as np

from .methods_common import indicator, make_registry, vector_to_scores

# Orizontul efectiv al uitării bayesiene: 1/(1−λ) ≈ 333 extrageri. Fixat a
# priori (nu calibrat pe rezultate — calibrarea pe același istoric evaluat de
# bench ar fi HARKing).
_DIRICHLET_FORGETTING = 0.997
_DIRICHLET_PRIOR = 0.5  # prior Jeffreys pe categoriile multinomialului

# η = 1 pentru log-loss (loss 1-mixabil): Hedge devine purtarea exponențială a
# verosimilității, echivalentul online al medierii bayesiene de modele.
_HEDGE_ETA = 1.0
_HEDGE_EPS = 1e-300  # podea numerică pentru log, fără -inf
_HEDGE_LAM_FAST = float(2.0 ** (-1.0 / 10.0))
_HEDGE_LAM_SLOW = float(2.0 ** (-1.0 / 100.0))
_HEDGE_SMOOTH_FREQ = 0.5   # Laplace pe contorii de frecvență și de tranziție
_HEDGE_SMOOTH_EWMA = 0.25  # podea EWMA: uniform la pornirea rece


def score_bayes_dirichlet_forget(draws_2d, max_num):
    """Media posterioară Dirichlet-multinomial cu uitare exponențială.

    α_i = prior + Σ_t λ^(vârstă t) · 1{i ∈ extragerea t} (vârsta 0 = ultima
    extragere); scorul = α/Σα, adică probabilitatea predictivă posterioară a
    fiecărui număr. Hiperparametrii sunt fixați a priori în modul.
    """
    ind = indicator(draws_2d, max_num)
    n, v = ind.shape
    if n == 0 or v == 0:
        return vector_to_scores(np.ones(max(v, 1)), max(v, 1))
    ages = np.arange(n - 1, -1, -1, dtype=np.float64)
    w = np.power(_DIRICHLET_FORGETTING, ages)
    alpha = _DIRICHLET_PRIOR + w @ ind
    return vector_to_scores(alpha / alpha.sum(), v)


def _hedge_predictives(
    v, counts, ewma_fast, ewma_slow, gap, trans, prev
) -> np.ndarray:
    """Distribuțiile predictive ale celor 6 experți din starea curentă.

    Fiecare rând e o distribuție normalizată peste cele v numere, calculată
    STRICT din extragerile deja văzute (starea nu include extragerea țintă).
    """
    p = np.empty((6, v), dtype=np.float64)
    p[0] = 1.0 / v  # uniform
    a1 = counts + _HEDGE_SMOOTH_FREQ
    p[1] = a1 / a1.sum()  # frecvență globală Laplace
    s2 = ewma_fast + _HEDGE_SMOOTH_EWMA
    p[2] = s2 / s2.sum()  # EWMA rapid (half-life 10)
    s3 = ewma_slow + _HEDGE_SMOOTH_EWMA
    p[3] = s3 / s3.sum()  # EWMA lent (half-life 100)
    g = 1.0 / (gap + 1.0)
    p[4] = g / g.sum()  # hazard de gol: mai „dator" = mai probabil
    if prev is None:
        p[5] = 1.0 / v
    else:
        row = trans[prev].sum(axis=0) + _HEDGE_SMOOTH_FREQ
        p[5] = row / row.sum()  # tranziții din ultima extragere
    return p


def score_hedge_experts_mixture(draws_2d, max_num):
    """Mixtură Hedge (η = 1, log-loss) peste șase predictori online simpli.

    La fiecare extragere t din prefix, fiecare expert e răsplătit cu log-ul
    probabilității pe care predictiva lui (din starea strict anterioară) a
    acordat-o numerelor extrase la t; ponderile se înmulțesc cu exp(η·r).
    Scorul final = mixtura predictivelor cu ponderile finale. Determinist,
    cauzal, invariant la ordinea numerelor în interiorul extragerii.
    """
    ind = indicator(draws_2d, max_num)
    n, v = ind.shape
    if n == 0 or v == 0:
        return vector_to_scores(np.ones(max(v, 1)), max(v, 1))
    counts = np.zeros(v, dtype=np.float64)
    ewma_fast = np.zeros(v, dtype=np.float64)
    ewma_slow = np.zeros(v, dtype=np.float64)
    gap = np.zeros(v, dtype=np.float64)
    trans = np.zeros((v, v), dtype=np.float64)
    prev = None
    logw = np.zeros(6, dtype=np.float64)  # ponderi în log: stabil numeric
    for t in range(n):
        p = _hedge_predictives(v, counts, ewma_fast, ewma_slow, gap, trans, prev)
        x = ind[t]
        r = (np.log(np.maximum(p, _HEDGE_EPS)) @ x) / max(float(x.sum()), 1.0)
        logw += _HEDGE_ETA * r
        logw -= logw.max()  # ancorează la 0 — aceleași ponderi, fără drift
        counts += x
        ewma_fast *= _HEDGE_LAM_FAST
        ewma_fast += x
        ewma_slow *= _HEDGE_LAM_SLOW
        ewma_slow += x
        gap += 1.0
        gap[x > 0] = 0.0
        cur = np.flatnonzero(x > 0)
        if prev is not None and cur.size and prev.size:
            trans[np.ix_(prev, cur)] += 1.0
        prev = cur
    p = _hedge_predictives(v, counts, ewma_fast, ewma_slow, gap, trans, prev)
    logw -= logw.max()
    w = np.exp(logw)
    w /= w.sum()
    return vector_to_scores(w @ p, v)


BAYES_METHODS = make_registry(
    [
        (
            "bayes_dirichlet_forget",
            score_bayes_dirichlet_forget,
            "bayes_online",
            "Dirichlet-multinomial online, uitare exponențială λ=0.997, prior Jeffreys",
        ),
        (
            "hedge_experts_mixture",
            score_hedge_experts_mixture,
            "ensemble_online",
            "Hedge η=1 pe log-loss peste 6 predictori online (uniform/freq/EWMA×2/gap/tranziții)",
        ),
    ]
)
