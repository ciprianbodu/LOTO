"""OMNIUS — meta-învățare adaptivă (învață care metodă e 'în formă' acum).

NU prezice numere (imposibil pe loterie aleatoare). În schimb, învață din ISTORIC
care metode de scoring au prins cel mai bine în extragerile RECENTE, le ponderează
după performanța lor recentă, și combină scorurile lor. Metodele care ratează pierd
pondere (învață din greșeli); cele 'în formă' câștigă.

Onest: pe termen lung performanța tot gravitează spre random (loto e aleator), DAR
meta-învățarea ('ce scorer să folosesc acum') are un tipar REAL — performanța
metodelor chiar variază în timp — spre deosebire de prezicerea numerelor.

Algoritm:
  1. Selectează un set de metode-candidat (rapide, diverse).
  2. Pe ultimele W extrageri, evaluează retroactiv fiecare metodă:
     pentru extragerea t, antrenează pe [0..t), prezice top-K, numără hituri.
  3. Scor de încredere per metodă = media hiturilor recente (cu decay exponențial:
     extragerile mai recente contează mai mult).
  4. Ponderi = softmax pe scorurile de încredere (metodele bune domină, dar nu
     monopolizează → diversificare).
  5. Scor final per număr = suma ponderată a scorurilor metodelor pe TOT istoricul.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Tuple, Callable

import numpy as np

logger = logging.getLogger(__name__)


def _normalize(scores: Dict[int, float], max_num: int) -> Dict[int, float]:
    if not scores:
        return {n: 0.0 for n in range(1, max_num + 1)}
    vals = np.fromiter(scores.values(), dtype=np.float64)
    vmin, vmax = float(vals.min()), float(vals.max())
    rng = max(vmax - vmin, 1e-12)
    out = {int(k): float((v - vmin) / rng) for k, v in scores.items()}
    for n in range(1, max_num + 1):
        out.setdefault(n, 0.0)
    return out


# Candidați: metode RAPIDE și DIVERSE (fără GPU, fără sklearn greu) ca meta-învățarea
# să fie rapidă. Acoperă familii distincte: frecvență, recență, markov, gap, spectral,
# bayesian, momentum, graf, teoria numerelor.
_OMNIUS_CANDIDATES = [
    "frequency", "recency", "weighted_recent", "momentum",
    "markov_1", "markov_2", "gap_poisson", "autocorr",
    "fourier", "ssa", "beta_binomial", "bayes_poisson",
    "pair_affinity", "centrality", "entropy_window",
    "sum_affinity", "modular", "digit_root", "decade_balance",
]


def _topk(scores: Dict[int, float], k: int) -> set:
    if not scores:
        return set()
    return set(n for n, _ in sorted(scores.items(), key=lambda x: x[1], reverse=True)[:k])


def score_omnius(draws_2d: np.ndarray, max_num: int) -> Dict[int, float]:
    """Meta-scorer: ponderează candidații după performanța lor recentă (învățare adaptivă)."""
    from .methods import METHODS  # lazy (evită circular import)

    n = draws_2d.shape[0]
    draw_n = draws_2d.shape[1]
    if n < 30:
        # prea puține date pt meta-învățare → fallback la frecvență simplă
        fn = METHODS.get("frequency")
        return fn[0](draws_2d, max_num) if fn else _normalize({}, max_num)

    # candidați disponibili în registry
    cands = [m for m in _OMNIUS_CANDIDATES if m in METHODS]
    if not cands:
        return _normalize({}, max_num)

    # ── 1. Evaluare retroactivă pe ultimele W extrageri (învățare din greșeli) ──
    W = min(40, n - 20)            # fereastra de "învățare"
    start = n - W
    decay = np.exp(np.linspace(-1.5, 0.0, W))  # extragerile recente cântăresc mai mult
    perf = {m: 0.0 for m in cands}
    wsum = 0.0

    for wi, t in enumerate(range(start, n)):
        history = draws_2d[:t]              # ce s-ar fi știut la momentul t
        actual = set(int(v) for v in draws_2d[t] if 1 <= int(v) <= max_num)
        w = float(decay[wi])
        wsum += w
        for m in cands:
            try:
                sc = METHODS[m][0](history, max_num)
            except Exception:  # noqa: BLE001
                continue
            if not sc:
                continue
            pred = _topk(sc, draw_n)        # ce ar fi pariat metoda
            hits = len(pred & actual)
            perf[m] += w * hits            # hituri ponderate cu recența

    # scor de încredere normalizat (hituri medii recente per metodă)
    conf = {m: perf[m] / max(wsum, 1e-9) for m in cands}

    # ── 2. Ponderi = softmax pe încredere (temperatură moderată → diversificare) ──
    vals = np.array([conf[m] for m in cands], dtype=np.float64)
    if vals.std() < 1e-9:
        weights = np.ones(len(cands)) / len(cands)
    else:
        z = (vals - vals.mean()) / (vals.std() + 1e-9)
        e = np.exp(z * 1.5)                 # temperatură 1.5: favorizează bunele, fără monopol
        weights = e / e.sum()

    # log: ce a "învățat" OMNIUS (top 3 metode în formă)
    ranked = sorted(zip(cands, weights), key=lambda x: -x[1])
    top = ", ".join(f"{m}={w:.2f}" for m, w in ranked[:3])
    logger.info(f"[OMNIUS] meta-învățare pe {W} extrageri → top: {top}")

    # ── 3. Scor final = combinație ponderată a scorurilor pe TOT istoricul ──
    final = np.zeros(max_num + 1, dtype=np.float64)
    for m, wgt in zip(cands, weights):
        try:
            sc = METHODS[m][0](draws_2d, max_num)
        except Exception:  # noqa: BLE001
            continue
        if not sc:
            continue
        # normalizăm scorul metodei la [0,1] înainte de combinare
        sc_n = _normalize(sc, max_num)
        for num, val in sc_n.items():
            final[num] += wgt * val

    return _normalize({k: float(final[k]) for k in range(1, max_num + 1)}, max_num)


OMNIUS_METHODS: Dict[str, Tuple[Callable, str, bool, str]] = {
    "omnius": (score_omnius, "meta-adaptive", False,
               "OMNIUS — meta-învățare: ponderează metodele după performanța recentă"),
}
