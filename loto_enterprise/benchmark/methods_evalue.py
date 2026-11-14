"""Metoda e-value / martingale de verosimilitate (1 metodă).

Al patrulea val (2026-11-14), la cererea utilizatorului („inventează o metodă
experimentală inteligentă"):

- `evalue_kelly_w104` — scorul fiecărui număr este logaritmul unei martingale
  de verosimilitate (e-value prequential, în sensul Shafer–Vovk) care pariază
  UNILATERAL pe „numărul iese mai des decât ipoteza nulă p0 = draw_n/max_num".
  La fiecare pas s din fereastră, cota estimată p̂_i(s) este calculată strict
  din trecut (predictibil), deci produsul parțial este un e-value valid:
  sub urnă corectă, speranța lui este cel mult 1 la ORICE timp de oprire —
  metoda nu poate acumula sistematic „dovadă" falsă. Sub o abatere reală
  p_i > p0, rata de creștere a log-ului tinde la D(Bern(p_i)‖Bern(p0)), care
  este rata maximă posibilă (Kelly/log-optimalitate): DACĂ ar exista un număr
  cald, această familie de detectori îl găsește asimptotic cel mai repede.

  Construcția concretă, cu toate constantele fixate a priori (anti-HARKing,
  aceeași convenție ca methods_bayes):
    • fereastră W = 104 extrageri (un an de 6/49; pe istorii mai scurte se
      folosește întregul istoric);
    • ancoră: p̄_i = frecvența lifetime DINAINTEA ferestrei (Laplace), cu
      β = 20 pseudo-extrageri — fereastra testează abaterea RECENTĂ raportată
      la comportamentul de lungă durată al aceluiași număr;
    • p̂_i(s) = max((β·p̄_i + c_i(s)) / (β + s), p0), c_i(s) = apariții în
      fereastră strict înainte de pasul s; constrângerea p̂ ≥ p0 face testul
      unilateral: un număr rece lasă multiplicatorul 1 (zero dovadă), nu
      acumulează scor „în direcția greșită";
    • multiplicator pe pas: M = (p̂/p0)^y · ((1−p̂)/(1−p0))^(1−y),
      scorul = Σ log M pe fereastră.

  IPOTEZĂ pentru bench, nu dovadă: intră în aceeași poartă walk-forward +
  corecție Holm ca restul registry-ului și nu este promovată în producție în
  afara deciziei existente. Deterministă, fără stare între apeluri, cauzală
  (folosește strict prefixul primit), numai NumPy, O(W·max_num) per apel.
"""

from __future__ import annotations

import numpy as np

from .methods_common import indicator, make_registry, vector_to_scores

# Constante fixate a priori (NU calibrate pe rezultatele evaluate de bench).
_EVALUE_WINDOW = 104   # un an de extrageri 6/49 (2/săptămână)
_EVALUE_BETA = 20.0    # pseudo-extrageri ale ancorei lifetime
_EVALUE_SMOOTH = 0.5   # Laplace pe frecvența lifetime dinaintea ferestrei


def score_evalue_kelly_w104(draws_2d, max_num):
    """E-value unilateral „cald", prequential, pe fereastra de W extrageri.

    Vezi docstringul modulului pentru construcție și proprietăți. Pe istoric
    vid scorurile sunt uniforme (nicio opinie).
    """
    ind = indicator(draws_2d, max_num)
    n, v = ind.shape
    if n == 0 or v == 0:
        return vector_to_scores(np.ones(max(v, 1)), max(v, 1))
    arr = np.asarray(draws_2d)
    draw_n = arr.shape[1] if arr.ndim == 2 else 1
    p0 = float(draw_n) / float(v)
    w = min(_EVALUE_WINDOW, n)
    win = ind[n - w :]  # (w, v) — capătul recent al prefixului
    prior = ind[: n - w]
    p_bar = (prior.sum(axis=0) + _EVALUE_SMOOTH) / (prior.shape[0] + 2.0 * _EVALUE_SMOOTH)
    # c[s, i] = apariții ale lui i în fereastră strict înainte de pasul s.
    c = np.vstack([np.zeros((1, v), dtype=np.float64), win.cumsum(axis=0)[:-1]])
    s = np.arange(w, dtype=np.float64)[:, None]
    phat = (_EVALUE_BETA * p_bar[None, :] + c) / (_EVALUE_BETA + s)
    phat = np.maximum(phat, p0)  # test unilateral: doar abaterile „cald" contează
    log_m = win * np.log(phat / p0) + (1.0 - win) * np.log((1.0 - phat) / (1.0 - p0))
    return vector_to_scores(log_m.sum(axis=0), v)


EVALUE_METHODS = make_registry(
    [
        (
            "evalue_kelly_w104",
            score_evalue_kelly_w104,
            "evalue_online",
            "Martingale de verosimilitate (e-value prequential, unilateral cald) "
            "pe 104 extrageri, ancorat la frecvența lifetime (β=20); Kelly "
            "log-optim sub abatere, E≤1 sub urnă corectă",
        ),
    ]
)
