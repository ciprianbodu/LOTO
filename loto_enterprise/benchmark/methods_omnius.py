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
import os
import time
from typing import Callable

import numpy as np

logger = logging.getLogger(__name__)


def _normalize(scores: dict[int, float], max_num: int) -> dict[int, float]:
    if not scores:
        return {n: 0.0 for n in range(1, max_num + 1)}
    vals = np.fromiter(scores.values(), dtype=np.float64)
    vmin, vmax = float(vals.min()), float(vals.max())
    rng = max(vmax - vmin, 1e-12)
    out = {int(k): float((v - vmin) / rng) for k, v in scores.items()}
    for n in range(1, max_num + 1):
        out.setdefault(n, 0.0)
    return out


# ── OMNIUS = meta-selector COMPREHENSIV: ponderează TOATE metodele MATEMATICE din
# registry (statistice/probabilistice/geometrice/teoria numerelor/Markov/spectrale...),
# DINAMIC, după performanța lor recentă pe istoric. Exclude GPU/neural/ML (lente,
# non-matematice) și meta/ensemble (recursie). Pentru a NU exploda în timp, evaluarea
# retroactivă are un BUGET: metodele rapide sunt mereu ponderate, cele lente (fit de
# model: statsmodels/SSA/Fourier/HMM) sunt evaluate ULTIMELE și sărite dacă se depășește
# bugetul. Astfel "toate metodele matematice posibile" sunt incluse, fără a-l face lent.
def _omnius_excluded_family(fam: str) -> bool:
    f = (fam or "").lower()
    return (f.startswith("nf-") or f.startswith("torch") or f.startswith("foundation")
            or f == "ssm" or f.startswith("ml-") or f.startswith("ensemble")
            or f == "meta-adaptive")


def _omnius_slowish_family(fam: str) -> bool:
    """Familii care pot fi LENTE (fit de model pe stația cu statsmodels) → evaluate ultimele."""
    f = (fam or "").lower()
    return (f.startswith("classical") or f == "spectral" or f == "hmm"
            or f == "llm-ts" or f == "association")


# metode excluse explicit: baseline pur-random, OMNIUS însuși (recursie), LLM (greu/torch)
_OMNIUS_DENYLIST = {"random", "omnius", "time_llm"}

_OMNIUS_CANDS_CACHE: list[str] | None = None


def _omnius_candidates() -> list[str]:
    """Listă DINAMICĂ de candidați = toate metodele matematice disponibile din registry
    (fără GPU/ML/neural/meta), ordonate rapid→lent. Cache-uită (calculată o dată)."""
    global _OMNIUS_CANDS_CACHE
    if _OMNIUS_CANDS_CACHE is not None:
        return _OMNIUS_CANDS_CACHE
    from .methods import METHODS  # lazy (evită circular import)
    pairs = []
    for name, tup in METHODS.items():
        fam = tup[1] if len(tup) > 1 else ""
        if name in _OMNIUS_DENYLIST or _omnius_excluded_family(fam):
            continue
        pairs.append((name, fam))
    # rapide întâi, posibil-lente la urmă; stabil alfabetic în fiecare grup
    pairs.sort(key=lambda nf: (_omnius_slowish_family(nf[1]), nf[0]))
    _OMNIUS_CANDS_CACHE = [n for n, _ in pairs]
    logger.info("[OMNIUS] %d metode matematice candidate (meta-selector comprehensiv)",
                len(_OMNIUS_CANDS_CACHE))
    return _OMNIUS_CANDS_CACHE


def _topk(scores: dict[int, float], k: int) -> set:
    if not scores:
        return set()
    return set(n for n, _ in sorted(scores.items(), key=lambda x: x[1], reverse=True)[:k])


def _smart_logic_hybrid(draws_2d: np.ndarray, max_num: int) -> dict[int, float]:
    """Smart Logic Hybrid v2 ca scorer standalone (replica formulei din engine, numpy pur):
    30% Gap (overdue) + 30% Recent-Hits + 15% Trend + 15% Frequency + 10% Positional."""
    n = draws_2d.shape[0]
    draw_n = draws_2d.shape[1]
    if n < 10:
        return _normalize({}, max_num)
    # matrice binară (max_num × n)
    bm = np.zeros((max_num + 1, n), dtype=np.float32)
    for i, row in enumerate(draws_2d):
        for v in row:
            vi = int(v)
            if 1 <= vi <= max_num:
                bm[vi, i] = 1.0
    freq = bm[1:].sum(axis=1)  # frecvență per număr
    fmax, fmin = (freq[freq > 0].max() if (freq > 0).any() else 1.0), (freq[freq > 0].min() if (freq > 0).any() else 0.0)
    w = min(20, n // 2)
    s_w, l_w = min(15, n), min(50, n)
    gap, trend, frq, pos, rec = {}, {}, {}, {}, {}
    for k in range(1, max_num + 1):
        idx = np.where(bm[k] > 0)[0]
        # Gap (overdue): current_gap / avg_gap, scalat 0.3-1.0
        if len(idx) < 2:
            gap[k] = 0.5
        else:
            avg_gap = float(np.diff(idx).mean()); cur = n - int(idx[-1])
            gap[k] = min(1.0, (cur / avg_gap) * 0.7 + 0.3) if avg_gap > 0 else 0.5
        # Trend: recent vs older window
        rf = float(bm[k, -w:].sum()); of = float(bm[k, -2*w:-w].sum()) if n >= 2*w else 0.0
        trend[k] = (0.6 if rf > 0 else 0.4) if of == 0 else min(1.0, max(0.0, (rf/of - 0.5) * 2))
        # Frequency: min-max
        frq[k] = (freq[k-1] - fmin) / max(fmax - fmin, 1e-9) if fmax != fmin else 0.5
        # Recent-Hits: 0.65*short + 0.35*long
        sr = float(bm[k, -s_w:].mean()) if s_w else 0.0
        lr = float(bm[k, -l_w:].mean()) if l_w else 0.0
        rec[k] = 0.65 * min(1.0, sr * 2.5) + 0.35 * min(1.0, lr * 3.0)
        # Positional: consistență (varianță mică = scor mare)
        counts = np.zeros(draw_n)
        for i, row in enumerate(draws_2d):
            for p, v in enumerate(row):
                if int(v) == k and p < draw_n:
                    counts[p] += 1
        tot = counts.sum()
        pos[k] = max(0.0, 1.0 - float(np.var(counts/tot)) * draw_n) if tot > 0 else 0.5
    final = {k: gap[k]*0.30 + rec[k]*0.30 + trend[k]*0.15 + frq[k]*0.15 + pos[k]*0.10
             for k in range(1, max_num + 1)}
    return _normalize(final, max_num)


def score_omnius(draws_2d: np.ndarray, max_num: int, budget_s: float | None = None) -> dict[int, float]:
    """OMNIUS = meta-selector COMPREHENSIV: ponderează TOATE metodele matematice din
    registry după performanța lor recentă (50%%) + Smart Logic Hybrid (50%%).

    `budget_s` = bugetul (sec) pt evaluarea retroactivă a candidaților. Metodele rapide
    sunt mereu evaluate; cele lente (statsmodels/SSA/Fourier/HMM) sunt evaluate ultimele
    și sărite dacă se depășește bugetul → OMNIUS rămâne mărginit. Default: env
    LOTO_OMNIUS_BUDGET_S sau 12s (UI-ul îi pasează ~2s ca biletul să fie instant)."""
    from .methods import METHODS  # lazy (evită circular import)

    n = draws_2d.shape[0]
    draw_n = draws_2d.shape[1]
    if n < 30:
        # prea puține date pt meta-învățare → doar Smart Logic
        return _smart_logic_hybrid(draws_2d, max_num)

    if budget_s is None:
        try:
            budget_s = float(os.environ.get("LOTO_OMNIUS_BUDGET_S", "12"))
        except (TypeError, ValueError):
            budget_s = 12.0

    # candidați = toate metodele matematice din registry (rapide întâi, lente la urmă)
    cands = [m for m in _omnius_candidates() if m in METHODS]
    if not cands:
        return _smart_logic_hybrid(draws_2d, max_num)

    # ── 1. Evaluare retroactivă pe ultimele W extrageri (învățare din performanța recentă) ──
    W = min(20, n - 20)            # fereastra de "învățare"
    start = n - W
    decay = np.exp(np.linspace(-1.5, 0.0, W))  # extragerile recente cântăresc mai mult
    wsum = float(decay.sum())
    actuals = [set(int(v) for v in draws_2d[t] if 1 <= int(v) <= max_num) for t in range(start, n)]

    # Buclă CANDIDAT-exterior + BUGET: fiecare metodă e evaluată pe TOATE ferestrele (corect)
    # sau deloc; odată depășit bugetul, metodele rămase (cele lente) sunt sărite din ponderare.
    conf: dict[str, float] = {}
    t0 = time.perf_counter()
    skipped = 0
    for mi, m in enumerate(cands):
        if conf and (time.perf_counter() - t0) > budget_s:
            skipped = len(cands) - mi
            break
        fn = METHODS[m][0]
        perf = 0.0
        ok = True
        for wi, t in enumerate(range(start, n)):
            try:
                sc = fn(draws_2d[:t], max_num)
            except Exception:  # noqa: BLE001
                ok = False
                break
            if sc:
                perf += float(decay[wi]) * len(_topk(sc, draw_n) & actuals[wi])
        if ok:
            conf[m] = perf / max(wsum, 1e-9)
    if not conf:
        return _smart_logic_hybrid(draws_2d, max_num)
    eval_cands = list(conf.keys())

    # ── 2. Ponderi = softmax pe încredere (temperatură moderată → diversificare) ──
    vals = np.array([conf[m] for m in eval_cands], dtype=np.float64)
    if vals.std() < 1e-9:
        weights = np.ones(len(eval_cands)) / len(eval_cands)
    else:
        z = (vals - vals.mean()) / (vals.std() + 1e-9)
        e = np.exp(z * 1.5)                 # temperatură 1.5: favorizează bunele, fără monopol
        weights = e / e.sum()

    ranked = sorted(zip(eval_cands, weights), key=lambda x: -x[1])
    top = ", ".join(f"{m}={w:.2f}" for m, w in ranked[:5])
    logger.info("[OMNIUS] meta-învățare: %d/%d metode ponderate (%d sărite buget) pe %d extrageri "
                "→ top5: %s", len(eval_cands), len(cands), skipped, W, top)

    # ── 3. Scor final = combinație ponderată a scorurilor pe TOT istoricul ──
    final = np.zeros(max_num + 1, dtype=np.float64)
    for m, wgt in zip(eval_cands, weights):
        try:
            sc = METHODS[m][0](draws_2d, max_num)
        except Exception:  # noqa: BLE001
            continue
        if not sc:
            continue
        sc_n = _normalize(sc, max_num)      # normalizăm la [0,1] înainte de combinare
        for num, val in sc_n.items():
            final[num] += wgt * val

    meta_scores = _normalize({k: float(final[k]) for k in range(1, max_num + 1)}, max_num)

    # ── 4. COMBINARE 50/50 cu Smart Logic Hybrid (Gap/Trend/Freq/Positional/Recent) ──
    smart = _smart_logic_hybrid(draws_2d, max_num)
    combined = {k: 0.5 * meta_scores.get(k, 0.0) + 0.5 * smart.get(k, 0.0)
                for k in range(1, max_num + 1)}
    return _normalize(combined, max_num)


def pick_omnius_ticket(
    pool: list[int],
    scores: dict[int, float],
    draw_n: int,
) -> list[int]:
    """Alege draw_n numere din pool: top după scor (fără filtre).

    Aliniat cu bench / walk-forward pe ținta 3+: fără constrângeri de paritate
    sau progresie aritmetică care abat biletul de la scorul validat.
    """
    draw_n = max(1, int(draw_n))
    ranked = sorted({int(n) for n in pool}, key=lambda n: scores.get(n, 0.0), reverse=True)
    return sorted(ranked[:draw_n])


OMNIUS_METHODS: dict[str, tuple[Callable, str, bool, str]] = {
    "omnius": (score_omnius, "meta-adaptive", False,
               "OMNIUS — meta-selector: ponderează TOATE metodele matematice după "
               "performanța recentă pe istoric (50%) + Smart Logic Hybrid (50%)"),
}
