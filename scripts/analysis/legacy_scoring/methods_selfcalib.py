"""Metodă cu învățare online și autocorectare (1 metodă).

Al cincilea val (2026-11-14), la cererea utilizatorului („metoda super
inteligentă să fie și prin învățare și autocorectare"):

- `selfcalib_online_logit` — predictor online pe două straturi, antrenat
  PREQUENTIAL (la fiecare pas prezice, apoi vede rezultatul și învață din
  propria greșeală, strict în această ordine):

  1. Stratul de bază: estimator Krichevsky–Trofimov cu uitare exponențială
     λ = 0.995 per număr — probabilitatea curentă că numărul iese,
     (α + contor_discountat) / (2α + masă_discountată), α = 0.5 (Jeffreys).
  2. Stratul de autocorectare: regresie logistică online peste 3 trăsături —
     logit(p_base), reziduul propriu recent e_i (EWMA γ = 0.9 al erorii
     „real − prezis": pozitiv = metoda a SUBESTIMAT sistematic numărul în
     ultima vreme) și termenul liber. Ponderile θ pornesc din priorul
     identitate (1, 0, 0) și sunt actualizate după FIECARE extragere cu un
     pas SGD pe log-loss (η = 0.5, normalizat cu 1/v ca magnitudinea
     actualizării să nu depindă de joc), cu tragere L2 (1e-3) spre prior —
     metoda nu poate aluneca definitiv pe zgomot, dar se poate calibra pe
     o deviație persistentă.

  „Autocorectarea" este literală: dacă stratul de bază greșește SISTEMATIC
  într-o direcție (supraestimează numerele proaspăt ieșite, subestimează
  numerele într-un regim nou), stratul doi învață ponderea care anulează
  acea eroare sistematică din reziduul măsurat pe propriile predicții.

  Proprietate onestă: pe urnă corectă reziduurile sunt zgomot pur, θ rămâne
  în jurul priorului și scorul revine la frecvența discountată — metoda nu
  poate inventa semnal; este o IPOTEZĂ pentru bench, nu o dovadă. Aceleași
  constante fixate a priori (anti-HARKing, convenția methods_bayes).
  Deterministă, fără stare între apeluri, cauzală (starea la pasul t folosește
  strict extragerile < t), numai NumPy, O(n·max_num) per apel — același
  profil de cost ca `hedge_experts_mixture`.
"""

from __future__ import annotations

import numpy as np

from loto_enterprise.benchmark.methods_common import indicator, make_registry, vector_to_scores

# Constante fixate a priori (NU calibrate pe rezultatele evaluate de bench).
_SC_LAMBDA = 0.995   # uitarea stratului de bază (half-life ≈ 139 extrageri)
_SC_ALPHA = 0.5      # netezidare Krichevsky–Trofimov (Jeffreys)
_SC_GAMMA = 0.9      # EWMA al reziduului propriu (half-life ≈ 7 extrageri)
_SC_ETA = 0.5        # rata de învățare SGD (normalizată cu 1/v)
_SC_L2 = 1e-3        # tragere spre priorul identitate θ0 = (1, 0, 0)


def _base_prob(d: np.ndarray, m: float, v: int) -> np.ndarray:
    """KT cu uitare: (α + d) / (2α + m); uniform la pornirea rece."""
    if m <= 0.0:
        return np.full(v, 1.0 / v, dtype=np.float64)
    return (_SC_ALPHA + d) / (2.0 * _SC_ALPHA + m)


def _predict(pb: np.ndarray, e: np.ndarray, theta: np.ndarray) -> np.ndarray:
    """Predicția calibrată q = σ(θ·x) pentru starea curentă (înainte de y)."""
    pb = np.clip(pb, 1e-6, 1.0 - 1e-6)
    x0 = np.log(pb) - np.log1p(-pb)  # logit(p_base)
    z = theta[0] * x0 + theta[1] * e + theta[2]
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30.0, 30.0)))


def score_selfcalib_online_logit(draws_2d, max_num):
    """Scor = probabilitatea calibrată pentru următoarea extragere.

    Bucla prequentială: la pasul t predicția folosește DOAR starea de după
    extragerile < t; abia apoi starea se actualizează cu extragerea t.
    """
    ind = indicator(draws_2d, max_num)
    n, v = ind.shape
    if n == 0 or v == 0:
        return vector_to_scores(np.ones(max(v, 1)), max(v, 1))
    d = np.zeros(v, dtype=np.float64)   # contor de apariții discount-at
    m = 0.0                             # masa totală discount-ată
    e = np.zeros(v, dtype=np.float64)   # EWMA al reziduului propriu (y − q)
    theta0 = np.array([1.0, 0.0, 0.0], dtype=np.float64)
    theta = theta0.copy()
    for t in range(n):
        pb = _base_prob(d, m, v)
        q = _predict(pb, e, theta)
        y = ind[t]
        err = y - q
        # SGD pe log-loss peste cele v ieșiri binare ale extragerii + L2 spre prior.
        pb_c = np.clip(pb, 1e-6, 1.0 - 1e-6)
        x0 = np.log(pb_c) - np.log1p(-pb_c)
        grad = np.array(
            [float((err * x0).sum()), float((err * e).sum()), float(err.sum())]
        ) / v - _SC_L2 * (theta - theta0)
        theta += _SC_ETA * grad
        # Actualizările de stare vin DUPĂ observarea lui y (ordine prequentială).
        e = _SC_GAMMA * e + (1.0 - _SC_GAMMA) * err
        d = _SC_LAMBDA * d + y
        m = _SC_LAMBDA * m + 1.0
    return vector_to_scores(_predict(_base_prob(d, m, v), e, theta), v)


SELFCALIB_METHODS = make_registry(
    [
        (
            "selfcalib_online_logit",
            score_selfcalib_online_logit,
            "learning_online",
            "KT cu uitare λ=0.995 + calibrare logistică online (SGD log-loss, "
            "η=0.5/v, L2→prior) pe logit(p_base) și reziduul propriu EWMA — "
            "învață din propriile erori după fiecare extragere",
        ),
    ]
)
