"""Metode matematice noi + factory blend-uri pentru căutare 6/49 rate_4plus @ k16.

Folosit de search_649_methods.py; după selecție, top-20 ajung în methods_top649.py.
"""
from __future__ import annotations

import itertools
import logging
from typing import Callable

import numpy as np

from .methods_graph import (
    score_graph_649_katz_community,
    score_graph_community_strength,
    score_graph_degree,
    score_graph_degree_recent,
    score_graph_eigenvector,
    score_graph_katz_high,
    score_graph_pagerank,
    score_graph_pagerank_recent,
    score_graph_rwr_recent,
    score_graph_second_order,
    score_graph_temporal_drift,
)

logger = logging.getLogger(__name__)


def _normalize(scores: dict[int, float], max_num: int) -> dict[int, float]:
    if not scores:
        return {n: 0.0 for n in range(1, max_num + 1)}
    vals = np.fromiter(scores.values(), dtype=np.float64)
    vals = vals[np.isfinite(vals)]
    if vals.size == 0:
        return {n: 0.0 for n in range(1, max_num + 1)}
    vmin, vmax = float(vals.min()), float(vals.max())
    rng = max(vmax - vmin, 1e-12)
    out = {int(k): float((v - vmin) / rng) if np.isfinite(v) else 0.0 for k, v in scores.items()}
    for n in range(1, max_num + 1):
        out.setdefault(n, 0.0)
    return out


def _indicator(draws_2d: np.ndarray, max_num: int) -> np.ndarray:
    arr = np.asarray(draws_2d)
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    T = arr.shape[0]
    M = np.zeros((max_num, T), dtype=np.float64)
    for t in range(T):
        for v in arr[t]:
            try:
                iv = int(v)
            except (TypeError, ValueError):
                continue
            if 1 <= iv <= max_num:
                M[iv - 1, t] = 1.0
    return M


def _gaps(draws_2d: np.ndarray, max_num: int) -> np.ndarray:
    """Gap curent (extrageri de la ultima apariție) per număr."""
    M = _indicator(draws_2d, max_num)
    T = M.shape[1]
    gaps = np.full(max_num, float(T), dtype=np.float64)
    for i in range(max_num):
        hits = np.where(M[i] > 0)[0]
        if hits.size:
            gaps[i] = T - 1 - int(hits[-1])
    return gaps


def _freq(draws_2d: np.ndarray, max_num: int, window: int | None = None) -> np.ndarray:
    M = _indicator(draws_2d, max_num)
    if window and M.shape[1] > window:
        M = M[:, -window:]
    return M.mean(axis=1)


def make_blend_scorer(parts: list[tuple[float, Callable]]) -> Callable:
    """Factory: listă (weight, score_fn) → scorer normalizat."""

    def _score(draws_2d, max_num: int) -> dict[int, float]:
        out = {n: 0.0 for n in range(1, max_num + 1)}
        for w, fn in parts:
            sc = fn(draws_2d, max_num)
            for n, v in sc.items():
                ni = int(n)
                if 1 <= ni <= max_num:
                    out[ni] += w * float(v)
        return _normalize(out, max_num)

    return _score


def register_blend(name: str, parts: list[tuple[float, Callable]], notes: str = "") -> None:
    from .methods import METHODS
    METHODS[name] = (make_blend_scorer(parts), "search-649-blend", False, notes or name)


# --------------------------------------------------------------------------- #
# Metode matematice noi (standalone)
# --------------------------------------------------------------------------- #

def score_649_gap_inverse(draws_2d, max_num):
    g = _gaps(draws_2d, max_num)
    sc = 1.0 / (1.0 + g)
    return _normalize({i + 1: float(sc[i]) for i in range(max_num)}, max_num)


def score_649_gap_sqrt(draws_2d, max_num):
    g = _gaps(draws_2d, max_num)
    sc = np.sqrt(g)
    return _normalize({i + 1: float(sc[i]) for i in range(max_num)}, max_num)


def score_649_wilson_lb(draws_2d, max_num, z: float = 1.96):
    M = _indicator(draws_2d, max_num)
    n = max(M.shape[1], 1)
    scores = {}
    for i in range(max_num):
        k = float(M[i].sum())
        phat = k / n
        denom = 1 + z * z / n
        centre = phat + z * z / (2 * n)
        margin = z * np.sqrt((phat * (1 - phat) + z * z / (4 * n)) / n)
        scores[i + 1] = max(0.0, (centre - margin) / denom)
    return _normalize(scores, max_num)


def score_649_beta_mean(draws_2d, max_num, alpha: float = 1.0, beta: float = 1.0):
    M = _indicator(draws_2d, max_num)
    n = max(M.shape[1], 1)
    scores = {}
    for i in range(max_num):
        k = float(M[i].sum())
        scores[i + 1] = (k + alpha) / (n + alpha + beta)
    return _normalize(scores, max_num)


def score_649_ewma_freq(draws_2d, max_num, halflife: float = 20.0):
    M = _indicator(draws_2d, max_num)
    T = M.shape[1]
    if T == 0:
        return _normalize({}, max_num)
    ages = np.arange(T)[::-1].astype(np.float64)
    w = 0.5 ** (ages / halflife)
    w /= w.sum()
    sc = (M * w).sum(axis=1)
    return _normalize({i + 1: float(sc[i]) for i in range(max_num)}, max_num)


def score_649_hmean_freq_recency(draws_2d, max_num):
    from .methods import score_frequency, score_recency
    f = score_frequency(draws_2d, max_num)
    r = score_recency(draws_2d, max_num)
    scores = {}
    for n in range(1, max_num + 1):
        a, b = float(f.get(n, 0)), float(r.get(n, 0))
        scores[n] = 2 * a * b / (a + b + 1e-12) if (a + b) > 0 else 0.0
    return _normalize(scores, max_num)


def score_649_gmean_freq_recency(draws_2d, max_num):
    from .methods import score_frequency, score_recency
    f = score_frequency(draws_2d, max_num)
    r = score_recency(draws_2d, max_num)
    scores = {n: float(np.sqrt(max(float(f.get(n, 0)), 0) * max(float(r.get(n, 0)), 0)))
              for n in range(1, max_num + 1)}
    return _normalize(scores, max_num)


def score_649_last_draw_neighbors(draws_2d, max_num):
    arr = np.asarray(draws_2d)
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    if arr.shape[0] < 1:
        return _normalize({}, max_num)
    last = set(int(x) for x in arr[-1] if 1 <= int(x) <= max_num)
    scores = {n: 0.0 for n in range(1, max_num + 1)}
    for a in last:
        for b in range(max(1, a - 3), min(max_num, a + 3) + 1):
            if b != a:
                scores[b] += 1.0
    return _normalize(scores, max_num)


def score_649_decade_hot(draws_2d, max_num):
    arr = np.asarray(draws_2d)
    w = min(40, arr.shape[0])
    recent = arr[-w:] if w else arr
    decade = np.zeros(10)
    for row in recent:
        for v in row:
            decade[min(9, max(0, (int(v) - 1) // 10))] += 1
    scores = {}
    for n in range(1, max_num + 1):
        scores[n] = float(decade[min(9, max(0, (n - 1) // 10))])
    return _normalize(scores, max_num)


def score_649_parity_recent(draws_2d, max_num):
    arr = np.asarray(draws_2d)
    w = min(30, arr.shape[0])
    recent = arr[-w:]
    odd = sum(1 for row in recent for v in row if int(v) % 2)
    even = sum(1 for row in recent for v in row if int(v) % 2 == 0)
    favor_odd = odd >= even
    scores = {n: 1.0 if (n % 2 == 1) == favor_odd else 0.3 for n in range(1, max_num + 1)}
    return _normalize(scores, max_num)


def score_649_low_high_balance(draws_2d, max_num):
    mid = max_num // 2
    f = _freq(draws_2d, max_num, window=50)
    low, high = f[:mid].sum(), f[mid:].sum()
    favor_low = low >= high
    scores = {n: float(f[n - 1]) * (1.2 if (n <= mid) == favor_low else 0.8) for n in range(1, max_num + 1)}
    return _normalize(scores, max_num)


def score_649_mod7_hot(draws_2d, max_num):
    arr = np.asarray(draws_2d)
    w = min(50, arr.shape[0])
    mod = np.zeros(7)
    for v in arr[-w:].ravel():
        mod[int(v) % 7] += 1
    scores = {n: float(mod[n % 7]) for n in range(1, max_num + 1)}
    return _normalize(scores, max_num)


def score_649_mod10_hot(draws_2d, max_num):
    arr = np.asarray(draws_2d)
    w = min(50, arr.shape[0])
    mod = np.zeros(10)
    for v in arr[-w:].ravel():
        mod[int(v) % 10] += 1
    scores = {n: float(mod[n % 10]) for n in range(1, max_num + 1)}
    return _normalize(scores, max_num)


def score_649_momentum(draws_2d, max_num, short: int = 15, long: int = 60):
    fs = _freq(draws_2d, max_num, short)
    fl = _freq(draws_2d, max_num, long)
    sc = fs - fl
    return _normalize({i + 1: float(sc[i]) for i in range(max_num)}, max_num)


def score_649_hazard_overdue(draws_2d, max_num):
    """Rata hazard empirică: frecvență / (1 + gap)."""
    g = _gaps(draws_2d, max_num)
    f = _freq(draws_2d, max_num)
    sc = f / (1.0 + g)
    return _normalize({i + 1: float(sc[i]) for i in range(max_num)}, max_num)


def score_649_volatility_low(draws_2d, max_num, window: int = 40):
    M = _indicator(draws_2d, max_num)
    if M.shape[1] < 5:
        return _normalize({}, max_num)
    w = min(window, M.shape[1])
    sub = M[:, -w:]
    var = sub.var(axis=1)
    sc = 1.0 / (1.0 + var)
    return _normalize({i + 1: float(sc[i]) for i in range(max_num)}, max_num)


def score_649_streak_boost(draws_2d, max_num):
    M = _indicator(draws_2d, max_num)
    T = M.shape[1]
    sc = np.zeros(max_num)
    for i in range(max_num):
        streak = 0
        for t in range(T - 1, -1, -1):
            if M[i, t]:
                streak += 1
            else:
                break
        sc[i] = streak
    return _normalize({i + 1: float(sc[i]) for i in range(max_num)}, max_num)


def score_649_cold_rebound(draws_2d, max_num, cold: int = 25):
    g = _gaps(draws_2d, max_num)
    sc = np.clip(g - cold, 0, None)
    return _normalize({i + 1: float(sc[i]) for i in range(max_num)}, max_num)


def score_649_rank_borda(draws_2d, max_num):
    from .methods import score_frequency, score_recency, score_random
    sources = [score_frequency, score_recency]
    try:
        from .methods_classical import score_seasonal_naive_week
        sources.append(score_seasonal_naive_week)
    except Exception:
        sources.append(score_random)
    ranks = np.zeros((len(sources), max_num))
    for si, fn in enumerate(sources):
        sc = fn(draws_2d, max_num)
        order = sorted(range(1, max_num + 1), key=lambda n: sc.get(n, 0), reverse=True)
        for ri, n in enumerate(order):
            ranks[si, n - 1] = max_num - ri
    borda = ranks.sum(axis=0)
    return _normalize({i + 1: float(borda[i]) for i in range(max_num)}, max_num)


def score_649_rrf_fusion(draws_2d, max_num, k: int = 30):
    bases = [
        score_graph_katz_high,
        score_graph_community_strength,
        score_graph_pagerank,
        score_graph_degree_recent,
    ]
    rrf = np.zeros(max_num)
    for fn in bases:
        sc = fn(draws_2d, max_num)
        order = sorted(range(1, max_num + 1), key=lambda n: sc.get(n, 0), reverse=True)
        for ri, n in enumerate(order):
            rrf[n - 1] += 1.0 / (k + ri + 1)
    return _normalize({i + 1: float(rrf[i]) for i in range(max_num)}, max_num)


def score_649_spectral_cooc(draws_2d, max_num):
    """Proiecție pe primul vector propriu al matricei de co-apariție brute."""
    arr = np.asarray(draws_2d)
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    T = arr.shape[0]
    C = np.zeros((max_num, max_num))
    for row in arr:
        nums = [int(x) for x in row if 1 <= int(x) <= max_num]
        for i in range(len(nums)):
            for j in range(i + 1, len(nums)):
                a, b = nums[i] - 1, nums[j] - 1
                C[a, b] += 1
                C[b, a] += 1
    try:
        w, v = np.linalg.eigh(C)
        vec = v[:, int(np.argmax(w))]
        vec = np.abs(vec)
    except Exception:
        vec = C.sum(axis=1)
    return _normalize({i + 1: float(vec[i]) for i in range(max_num)}, max_num)


def score_649_draw_sum_reversion(draws_2d, max_num):
    arr = np.asarray(draws_2d)
    if arr.shape[0] < 10:
        return _normalize({}, max_num)
    sums = arr.sum(axis=1)
    target = float(np.median(sums[-30:]))
    mean_num = target / arr.shape[1]
    scores = {n: 1.0 / (1.0 + abs(n - mean_num)) for n in range(1, max_num + 1)}
    return _normalize(scores, max_num)


def score_649_consecutive_penalty(draws_2d, max_num):
    f = _freq(draws_2d, max_num, window=80)
    scores = {}
    for n in range(1, max_num + 1):
        penalty = 0.0
        if n > 1 and f[n - 2] > f[n - 1]:
            penalty += 0.15
        if n < max_num and f[n] > f[n - 1]:
            penalty += 0.15
        scores[n] = max(0.0, float(f[n - 1]) - penalty)
    return _normalize(scores, max_num)


def score_649_triple_graph_classic(draws_2d, max_num):
    return make_blend_scorer([
        (0.45, score_graph_katz_high),
        (0.30, score_graph_community_strength),
        (0.25, score_649_hazard_overdue),
    ])(draws_2d, max_num)


# EWMA halflife variants
def _ewma_hl(hl: float):
    def _fn(draws_2d, max_num):
        return score_649_ewma_freq(draws_2d, max_num, halflife=hl)
    _fn.__name__ = f"score_649_ewma_{int(hl)}"
    return _fn


# Momentum variants
def _mom(s: int, l: int):
    def _fn(draws_2d, max_num):
        return score_649_momentum(draws_2d, max_num, short=s, long=l)
    _fn.__name__ = f"score_649_mom_{s}_{l}"
    return _fn


SEARCH_649_NEW: dict[str, tuple[Callable, str, bool, str]] = {
    "649_gap_inverse":       (score_649_gap_inverse,       "math-649", False, "Inverse gap (overdue)"),
    "649_gap_sqrt":          (score_649_gap_sqrt,          "math-649", False, "Sqrt gap overdue"),
    "649_wilson_lb":         (score_649_wilson_lb,         "math-649", False, "Wilson lower bound freq"),
    "649_beta_mean":         (score_649_beta_mean,         "math-649", False, "Beta posterior mean"),
    "649_ewma_10":           (_ewma_hl(10),                "math-649", False, "EWMA freq halflife 10"),
    "649_ewma_20":           (_ewma_hl(20),                "math-649", False, "EWMA freq halflife 20"),
    "649_ewma_40":           (_ewma_hl(40),                "math-649", False, "EWMA freq halflife 40"),
    "649_ewma_80":           (_ewma_hl(80),                "math-649", False, "EWMA freq halflife 80"),
    "649_hmean_freq_rec":    (score_649_hmean_freq_recency, "math-649", False, "Harmonic mean freq+recency"),
    "649_gmean_freq_rec":    (score_649_gmean_freq_recency, "math-649", False, "Geometric mean freq+recency"),
    "649_last_neighbors":    (score_649_last_draw_neighbors, "math-649", False, "Neighbors of last draw"),
    "649_decade_hot":        (score_649_decade_hot,        "math-649", False, "Hot decade buckets"),
    "649_parity_recent":     (score_649_parity_recent,     "math-649", False, "Parity trend match"),
    "649_low_high_bal":      (score_649_low_high_balance,  "math-649", False, "Low/high balance"),
    "649_mod7_hot":          (score_649_mod7_hot,          "math-649", False, "Mod-7 hot residues"),
    "649_mod10_hot":         (score_649_mod10_hot,         "math-649", False, "Mod-10 hot endings"),
    "649_mom_10_40":         (_mom(10, 40),                "math-649", False, "Freq momentum 10 vs 40"),
    "649_mom_15_60":         (_mom(15, 60),                "math-649", False, "Freq momentum 15 vs 60"),
    "649_mom_20_80":         (_mom(20, 80),                "math-649", False, "Freq momentum 20 vs 80"),
    "649_hazard_overdue":    (score_649_hazard_overdue,    "math-649", False, "Hazard = freq/(1+gap)"),
    "649_volatility_low":    (score_649_volatility_low,    "math-649", False, "Low volatility preference"),
    "649_streak_boost":      (score_649_streak_boost,      "math-649", False, "Recent hit streak"),
    "649_cold_rebound":      (score_649_cold_rebound,      "math-649", False, "Cold number rebound"),
    "649_rank_borda":        (score_649_rank_borda,        "math-649", False, "Borda rank fusion"),
    "649_rrf_graph":         (score_649_rrf_fusion,        "math-649", False, "RRF graph fusion"),
    "649_spectral_cooc":     (score_649_spectral_cooc,     "math-649", False, "Spectral co-occurrence"),
    "649_sum_reversion":     (score_649_draw_sum_reversion, "math-649", False, "Draw sum reversion"),
    "649_consec_penalty":    (score_649_consecutive_penalty, "math-649", False, "Anti-consecutive pairs"),
    "649_triple_graph_classic": (score_649_triple_graph_classic, "math-649", False, "Katz+community+hazard"),
}


# Baze pentru blend-uri (nume registry → fn)
BLEND_BASE_NAMES: list[str] = [
    "graph_649_katz_community", "graph_katz_high", "graph_community_strength",
    "graph_pagerank", "graph_pagerank_recent", "graph_degree", "graph_degree_recent",
    "graph_eigenvector", "graph_rwr_recent", "graph_second_order", "graph_temporal_drift",
    "649_hazard_overdue", "649_wilson_lb", "649_gap_inverse", "649_ewma_20",
    "649_mom_15_60", "649_rrf_graph",     "649_triple_graph_classic", "649_hmean_freq_rec",
    "seasonal_naive", "prime_bias", "gap_poisson", "markov_1", "markov_2",
    "beta_binomial", "frequency", "recency",
]

BLEND_WEIGHTS = (0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85)


def _resolve_fn(name: str) -> Callable | None:
    from .methods import METHODS, method_meta
    name = name.replace("649_triple_graph_classical", "649_triple_graph_classic")
    if name not in METHODS and name in SEARCH_649_NEW:
        METHODS[name] = SEARCH_649_NEW[name]
    if name not in METHODS:
        return None
    fn = METHODS[name][0]
    if getattr(fn, "_unavailable_reason", None):
        return None
    return fn


def generate_blend_candidates(max_blends: int = 420) -> dict[str, tuple[Callable, str, bool, str]]:
    """Generează blend-uri 2- și 3-componente din baze cunoscute."""
    bases: list[tuple[str, Callable]] = []
    for nm in BLEND_BASE_NAMES:
        fn = _resolve_fn(nm)
        if fn is not None:
            bases.append((nm, fn))
    out: dict[str, tuple[Callable, str, bool, str]] = {}
    count = 0
    # Pairwise blends
    for (n1, f1), (n2, f2) in itertools.combinations(bases, 2):
        for w1 in BLEND_WEIGHTS:
            w2 = 1.0 - w1
            if w2 <= 0.05:
                continue
            name = f"649_blend_{n1[:12]}_{int(w1*100)}_{n2[:12]}"
            if name in out:
                continue
            out[name] = (
                make_blend_scorer([(w1, f1), (w2, f2)]),
                "search-649-blend",
                False,
                f"{w1:.0%} {n1} + {w2:.0%} {n2}",
            )
            count += 1
            if count >= max_blends:
                return out
    # Triple blends (top graph bases)
    top3 = [b for b in bases if b[0].startswith("graph") or b[0].startswith("649")][:12]
    for combo in itertools.combinations(top3, 3):
        for w1 in (0.5, 0.4, 0.35):
            for w2 in (0.3, 0.35, 0.25):
                w3 = 1.0 - w1 - w2
                if w3 < 0.1:
                    continue
                name = f"649_tri_{combo[0][0][:8]}_{combo[1][0][:8]}_{int(w1*100)}"
                if name in out:
                    continue
                out[name] = (
                    make_blend_scorer([(w1, combo[0][1]), (w2, combo[1][1]), (w3, combo[2][1])]),
                    "search-649-blend",
                    False,
                    f"tri blend {combo[0][0]}+{combo[1][0]}+{combo[2][0]}",
                )
                count += 1
                if count >= max_blends:
                    return out
    return out


def load_search_registry(include_existing: bool = True, max_blends: int = 420) -> list[str]:
    """Încarcă metode noi + blend-uri în METHODS; returnează lista de nume de evaluat."""
    from .methods import METHODS, list_methods, method_meta
    from .disabled import load_disabled
    disabled = load_disabled()
    for nm, tup in SEARCH_649_NEW.items():
        if nm not in METHODS:
            METHODS[nm] = tup
    blends = generate_blend_candidates(max_blends=max_blends)
    for nm, tup in blends.items():
        if nm not in METHODS:
            METHODS[nm] = tup
    names: list[str] = list(SEARCH_649_NEW.keys()) + list(blends.keys())
    if include_existing:
        for nm in list_methods():
            if nm in disabled:
                continue
            meta = method_meta(nm)
            if not meta.get("available", True):
                continue
            if nm not in names:
                names.append(nm)
    return names


def merge_search_into_methods() -> int:
    """Înregistrează SEARCH_649_NEW în METHODS (fără blend-uri efemere)."""
    from .methods import METHODS
    added = 0
    for nm, tup in SEARCH_649_NEW.items():
        if nm not in METHODS:
            METHODS[nm] = tup
            added += 1
    return added
