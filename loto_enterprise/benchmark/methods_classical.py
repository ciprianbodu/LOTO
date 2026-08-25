"""Classical statistical + Markov + Bayesian + spectral prediction methods.

Each scorer respects the same interface as methods.py:
    score_xxx(draws_2d: np.ndarray, max_num: int) -> dict[int, float]

All methods here are CPU-friendly (no GPU required). Lazy imports — if a
library is missing, the scorer returns {} and the method is marked unavailable.
"""
from __future__ import annotations

import logging
import warnings
from typing import Callable

import numpy as np

warnings.filterwarnings("ignore")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared helpers (copy of utilities so this module is self-contained)
# ---------------------------------------------------------------------------

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


def _build_binary(draws_2d: np.ndarray, max_num: int) -> np.ndarray:
    """(n_draws, draw_n) → (max_num, n_draws) binary indicator."""
    n_draws = draws_2d.shape[0]
    bm = np.zeros((max_num, n_draws), dtype=np.float32)
    for i, row in enumerate(draws_2d):
        for v in row:
            vi = int(v)
            if 1 <= vi <= max_num:
                bm[vi - 1, i] = 1.0
    return bm


def _unavailable(reason: str) -> Callable:
    def _score(draws_2d, max_num):
        return {}
    _score._unavailable_reason = reason  # type: ignore[attr-defined]
    return _score


# ===========================================================================
# STATSFORECAST — many auto methods at once with similar API
# ===========================================================================

_STATSF_OK: bool | None = None
_STATSF_ERR: str | None = None


def _check_statsforecast() -> bool:
    global _STATSF_OK, _STATSF_ERR
    if _STATSF_OK is not None:
        return _STATSF_OK
    try:
        import statsforecast  # noqa: F401
        from statsforecast import StatsForecast  # noqa: F401
        _STATSF_OK = True
    except Exception as exc:
        _STATSF_OK = False
        _STATSF_ERR = f"{type(exc).__name__}: {exc}"
    return _STATSF_OK


def _statsforecast_per_number(draws_2d, max_num, model_factory, context: int = 256) -> dict[int, float]:
    """Run a statsforecast model per number on its binary indicator series.

    `model_factory` is a callable returning a fresh model instance per series.
    Returns 1-step-ahead probabilities (after sigmoid-like clipping) per number.
    """
    if not _check_statsforecast():
        return {}
    if draws_2d.shape[0] < 20:
        return {}
    binary = _build_binary(draws_2d, max_num)
    ctx = min(context, binary.shape[1])
    scores: dict[int, float] = {}
    for i in range(max_num):
        series = binary[i, -ctx:].astype(np.float32)
        if series.sum() < 2:
            scores[i + 1] = 0.0
            continue
        try:
            m = model_factory()
            m.fit(series)
            yhat = m.predict(h=1)
            if isinstance(yhat, dict):
                val = float(next(iter(yhat.values()))[0])
            else:
                val = float(np.asarray(yhat).ravel()[0])
            scores[i + 1] = max(0.0, min(1.0, val))
        except Exception:
            scores[i + 1] = float(series.mean())
    return _normalize(scores, max_num)


def score_croston_classic(draws_2d, max_num):
    if not _check_statsforecast():
        return {}
    try:
        from statsforecast.models import CrostonClassic
        return _statsforecast_per_number(draws_2d, max_num, lambda: CrostonClassic())
    except Exception as exc:
        logger.debug(f"[croston_classic] {exc}")
        return {}


def score_croston_sba(draws_2d, max_num):
    if not _check_statsforecast():
        return {}
    try:
        from statsforecast.models import CrostonSBA
        return _statsforecast_per_number(draws_2d, max_num, lambda: CrostonSBA())
    except Exception as exc:
        logger.debug(f"[croston_sba] {exc}")
        return {}


def score_seasonal_naive_week(draws_2d, max_num):
    """Lottery extracts often weekly — value from N steps ago (default 1 week ≈ 1 draw)."""
    binary = _build_binary(draws_2d, max_num)
    n = binary.shape[1]
    if n < 2:
        return _normalize({n: 0.5 for n in range(1, max_num + 1)}, max_num)
    lag = min(7, n - 1)
    return _normalize({i + 1: float(binary[i, -lag]) for i in range(max_num)}, max_num)


# ===========================================================================
# MARKOV CHAINS & N-GRAMS — sequence models
# ===========================================================================

def _markov_score(draws_2d, max_num, order: int = 1, decay: float = 0.05) -> dict[int, float]:
    """K-th order Markov chain on binary appearance: P(num appears | last K draws)."""
    if draws_2d.shape[0] <= order:
        return {}
    binary = _build_binary(draws_2d, max_num)  # (max_num, n)
    n = binary.shape[1]
    # For each number, conditional P(appear_t | appear_t-1..t-order)
    scores: dict[int, float] = {}
    weights = np.exp(-decay * np.arange(n - order)[::-1]).astype(np.float64)
    for i in range(max_num):
        s = binary[i]
        # Last `order` values define current state
        cur_state = tuple(int(x) for x in s[-order:])
        # Count weighted transitions where prior `order` matches current state
        num = 0.0
        den = 0.0
        for t in range(n - order):
            state = tuple(int(x) for x in s[t:t + order])
            if state == cur_state:
                den += weights[t]
                num += weights[t] * float(s[t + order])
        scores[i + 1] = num / den if den > 0 else float(s.mean())
    return _normalize(scores, max_num)


def score_markov_2(draws_2d, max_num):
    """Helper (NU în METHODS): folosit de blend-uri TOP649. `markov_2` blacklistat."""
    return _markov_score(draws_2d, max_num, order=2)


# ===========================================================================
# BAYESIAN PRIORS
# ===========================================================================

def score_beta_binomial(draws_2d, max_num):
    """Helper (NU în METHODS): folosit de blend-uri TOP649. `beta_binomial` blacklistat.

    Beta-Binomial conjugate: prior Beta(α,β), posterior mean (α+k)/(α+β+n).
    Uses recency-weighted observations."""
    if draws_2d.shape[0] == 0:
        return {}
    binary = _build_binary(draws_2d, max_num)
    n = binary.shape[1]
    weights = np.exp(np.linspace(-2.0, 0.0, n))
    weights /= weights.sum()
    expected_p = float(draws_2d.shape[1] / max_num)  # prior mean
    alpha_0 = 2.0 * expected_p
    beta_0 = 2.0 * (1.0 - expected_p)
    scores: dict[int, float] = {}
    for i in range(max_num):
        s = binary[i]
        eff_n = float(weights.sum() * len(s))
        eff_k = float((s * weights * len(s)).sum())
        scores[i + 1] = (alpha_0 + eff_k) / (alpha_0 + beta_0 + eff_n)
    return _normalize(scores, max_num)


def score_bayesian_poisson(draws_2d, max_num):
    """Bayesian Poisson rate: counts in window → posterior under Gamma prior."""
    if draws_2d.shape[0] == 0:
        return {}
    binary = _build_binary(draws_2d, max_num)
    n = binary.shape[1]
    window = min(60, n)
    recent = binary[:, -window:]
    # Gamma(a, b) prior with a=1, b=2 (rate ~ 0.5)
    a0, b0 = 1.0, 2.0
    scores: dict[int, float] = {}
    for i in range(max_num):
        k = float(recent[i].sum())
        # Posterior mean rate
        rate = (a0 + k) / (b0 + window)
        scores[i + 1] = rate
    return _normalize(scores, max_num)


def score_negative_binomial(draws_2d, max_num):
    """Negative Binomial: models overdispersion in occurrence counts."""
    binary = _build_binary(draws_2d, max_num)
    n = binary.shape[1]
    if n == 0:
        return {}
    window = min(80, n)
    recent = binary[:, -window:]
    scores: dict[int, float] = {}
    for i in range(max_num):
        counts = float(recent[i].sum())
        mean = counts / window
        # NB variance > mean (overdispersion). Score = mean adjusted by inverse-spread.
        var = float(recent[i].var()) + 1e-9
        scores[i + 1] = mean / (1.0 + var)
    return _normalize(scores, max_num)


# ===========================================================================
# SPECTRAL / DECOMPOSITION
# ===========================================================================

def score_fourier_top_k(draws_2d, max_num):
    """FFT top-K frequencies — reconstruct signal, predict next step."""
    binary = _build_binary(draws_2d, max_num)
    n = binary.shape[1]
    if n < 16:
        return {}
    scores: dict[int, float] = {}
    for i in range(max_num):
        s = binary[i].astype(np.float32)
        # FFT
        spec = np.fft.rfft(s)
        # Keep top-3 amplitudes
        amp = np.abs(spec)
        if len(amp) < 3:
            scores[i + 1] = float(s.mean())
            continue
        top_idx = np.argsort(amp)[-3:]
        mask = np.zeros_like(spec)
        mask[top_idx] = spec[top_idx]
        # Reconstruct + extrapolate one step
        recon = np.fft.irfft(mask, n=n + 1)
        scores[i + 1] = float(recon[-1])
    return _normalize(scores, max_num)


def score_dmd_basic(draws_2d, max_num):
    """Dynamic Mode Decomposition on stacked recent windows."""
    binary = _build_binary(draws_2d, max_num).astype(np.float64)  # (max_num, n)
    n = binary.shape[1]
    if n < 20:
        return {}
    L = min(10, n // 4)
    # Build snapshot matrices X (cols = past), Y (cols = next)
    snaps = np.stack([binary[:, k:k + L] for k in range(n - L)], axis=-1)  # (max_num, L, K)
    X = snaps.reshape(max_num * L, -1)[:, :-1]
    Y = snaps.reshape(max_num * L, -1)[:, 1:]
    try:
        # Low-rank DMD
        U, S, Vt = np.linalg.svd(X, full_matrices=False)
        r = min(5, len(S))
        U_r = U[:, :r]
        S_r = np.diag(S[:r])
        V_r = Vt[:r].T
        A_tilde = U_r.T @ Y @ V_r @ np.linalg.inv(S_r)
        # Eigendecomposition
        w, _ = np.linalg.eig(A_tilde)
        # Project last snapshot forward
        last_snap = binary[:, -L:].ravel()
        proj = U_r.T @ last_snap
        forward = U_r @ (A_tilde @ proj)
        # Take last column = predicted next step
        forward_resh = forward.reshape(max_num, L)
        scores = {i + 1: float(forward_resh[i, -1]) for i in range(max_num)}
        return _normalize(scores, max_num)
    except Exception as exc:
        logger.debug(f"[dmd] {exc}")
        return {}


# ===========================================================================
# Holt-Winters fallback (statsmodels)
# ===========================================================================

def _check_statsmodels() -> bool:
    try:
        import statsmodels  # noqa: F401
        return True
    except Exception:
        return False


# ===========================================================================
# Registry of new classical methods
# ===========================================================================
# EXTRA matematice / geometrice (numpy pur, CPU, fără librării noi) 2026-05-31
# ===========================================================================

def score_gap_poisson(draws_2d, max_num):
    """Helper (NU în METHODS): folosit de blend-uri TOP649. `gap_poisson` blacklistat.

    Overdue prin model Poisson pe gap-uri: P(număr e 'datorat') ~ gap_curent/gap_mediu."""
    bm = _build_binary(draws_2d, max_num)
    n = bm.shape[1]
    scores = {}
    for i in range(max_num):
        idx = np.where(bm[i] > 0)[0]
        if len(idx) < 2:
            scores[i + 1] = 0.5
            continue
        gaps = np.diff(idx)
        avg_gap = float(gaps.mean()) if len(gaps) else n
        cur_gap = n - int(idx[-1]) - 1
        scores[i + 1] = min(1.0, cur_gap / max(avg_gap, 1e-6))
    return _normalize(scores, max_num)


def score_autocorr(draws_2d, max_num):
    """Autocorelație lag-1..5 pe seria binară: numere cu auto-corelație pozitivă (revin ciclic)."""
    bm = _build_binary(draws_2d, max_num)
    scores = {}
    for i in range(max_num):
        s = bm[i] - bm[i].mean()
        denom = float((s * s).sum()) + 1e-9
        ac = 0.0
        for lag in range(1, 6):
            if len(s) > lag:
                ac += float((s[:-lag] * s[lag:]).sum()) / denom
        scores[i + 1] = ac
    return _normalize(scores, max_num)


def score_pair_affinity(draws_2d, max_num):
    """Afinitate de co-apariție: numere care apar des împreună cu numerele recente (graf de co-ocurență)."""
    n_draws = draws_2d.shape[0]
    if n_draws < 5:
        return {}
    co = np.zeros((max_num + 1, max_num + 1), dtype=np.float64)
    for row in draws_2d:
        nums = [int(v) for v in row if 1 <= int(v) <= max_num]
        for a in nums:
            for b in nums:
                if a != b:
                    co[a, b] += 1.0
    # numere recente (ultima fereastră)
    recent = set()
    for row in draws_2d[-10:]:
        recent.update(int(v) for v in row if 1 <= int(v) <= max_num)
    scores = {}
    for k in range(1, max_num + 1):
        scores[k] = float(sum(co[k, r] for r in recent))
    return _normalize(scores, max_num)


# ===========================================================================
# TEORIA NUMERELOR + SPREAD/SUME POZITIONALE (numpy, CPU) 2026-05-31
# ===========================================================================

def _draw_sums(draws_2d) -> np.ndarray:
    """Suma fiecărei extrageri (ignoră padding 0)."""
    return np.array(
        [sum(int(v) for v in row if int(v) > 0) for row in draws_2d],
        dtype=np.float64,
    )


def score_sum_affinity(draws_2d, max_num):
    """Afinitate EMPIRICĂ cu suma tipică: cât de des apare k în extrageri a căror
    SUMĂ e aproape de media istorică (gaussian pe suma extragerii, nu pe k).

    Varianta veche era ``exp(-((k − mean/n)² / …))`` — o gaussiană pe axa 1…N,
    independentă de istoricul lui k. Pe Joker 5/45 vârful e ~23, deci top-11 era
    MEREU 18–28 consecutiv. Asta nu e afinitate, e distanță la medie.
    Un număr mic poate intra într-o extragere cu sumă tipică alături de numere
    mari; scorul trebuie să reflecte asta, nu |k − 23|.
    """
    if draws_2d.shape[0] < 5:
        return {}
    sums = _draw_sums(draws_2d)
    mean_sum = float(sums.mean())
    std_sum = float(sums.std()) + 1e-9
    typicality = np.exp(-0.5 * ((sums - mean_sum) / std_sum) ** 2)
    # (max_num, n_draws) @ (n_draws,) → masă de extrageri cu sumă tipică care conțin k
    raw = _build_binary(draws_2d, max_num).astype(np.float64) @ typicality
    scores = {}
    for k in range(1, max_num + 1):
        v = float(raw[k - 1])
        scores[k] = v if np.isfinite(v) else 0.0
    return _normalize(scores, max_num)


def score_parity_balance(draws_2d, max_num):
    """Echilibru par/impar: clasa cerută + frecvență în clasă (tie-break).

    Fără tie-break, scorul are doar 2 nivele → top-K degeneră în „cele mai
    mari pare/impare" (rank_by_score: număr desc), nu un pool util. Același
    pattern ca ``prime_bias``. Clasa rămâne axa principală; frecvența rupe
    egalitățile din aceeași clasă.
    """
    if draws_2d.shape[0] < 5:
        return {}
    # raport mediu de pare per extragere
    even_counts = [sum(1 for v in row if int(v) % 2 == 0 and v > 0) for row in draws_2d]
    avg_even = float(np.mean(even_counts))
    draw_n = draws_2d.shape[1]
    target_even_ratio = avg_even / max(draw_n, 1)
    # ultima extragere: cate pare a avut → ce lipseste
    last = draws_2d[-1]
    last_even = sum(1 for v in last if int(v) % 2 == 0 and v > 0)
    need_even = target_even_ratio > (last_even / max(draw_n, 1))
    freq = np.zeros(max_num + 1, dtype=np.float64)
    for row in draws_2d:
        for v in row:
            vi = int(v)
            if 1 <= vi <= max_num:
                freq[vi] += 1.0
    fmax = float(freq.max()) or 1.0
    scores = {}
    for k in range(1, max_num + 1):
        is_even = (k % 2 == 0)
        base = 1.0 if (is_even == need_even) else 0.4
        # 0.01 << diferența de clasă (0.6) → clasa domină, frecvența rupe egalitățile.
        scores[k] = base + 0.01 * (freq[k] / fmax)
    return _normalize(scores, max_num)


def score_prime_bias(draws_2d, max_num):
    """Bias prime/compuse + frecvență în clasă (tie-break).

    Fără tie-break, scorul are doar 2 nivele → top-K degeneră în „cele mai mici
    N compuse/prime” (ordine numerică), nu un pool util.
    """
    if draws_2d.shape[0] < 5:
        return {}
    def is_prime(n):
        if n < 2: return False
        for d in range(2, int(n ** 0.5) + 1):
            if n % d == 0: return False
        return True
    primes = {k for k in range(1, max_num + 1) if is_prime(k)}
    w = min(40, draws_2d.shape[0])
    p_hits = c_hits = 0
    for row in draws_2d[-w:]:
        for v in row:
            vi = int(v)
            if vi > 0:
                if vi in primes: p_hits += 1
                else: c_hits += 1
    prime_rate = p_hits / max(p_hits + c_hits, 1)
    freq = np.zeros(max_num + 1, dtype=np.float64)
    for row in draws_2d:
        for v in row:
            vi = int(v)
            if 1 <= vi <= max_num:
                freq[vi] += 1.0
    fmax = float(freq.max()) or 1.0
    scores = {}
    for k in range(1, max_num + 1):
        base = prime_rate if k in primes else (1.0 - prime_rate)
        # 0.01 << diferența tipică între clase (~0.2–0.5) → clasa domină, frecvența
        # doar rupe egalitățile din aceeași clasă.
        scores[k] = base + 0.01 * (freq[k] / fmax)
    return _normalize(scores, max_num)


# ===========================================================================

CLASSICAL_METHODS: dict[str, tuple[Callable, str, bool, str]] = {
    "sum_affinity":    (score_sum_affinity,    "geometric-sum",   False, "Afinitate empirica cu suma tipica (nu gaussian pe axa 1..N)"),
    "parity_balance":  (score_parity_balance,  "geometric-parity", False, "Echilibru par/impar"),
    "prime_bias":      (score_prime_bias,      "number-theory",   False, "Bias prime vs compuse"),
    "autocorr":        (score_autocorr,        "math-autocorr",  False, "Autocorelatie lag 1-5"),
    "pair_affinity":   (score_pair_affinity,   "geometric-graph", False, "Co-aparitie cu numerele recente"),
    "croston_classic": (score_croston_classic, "classical-intermittent", False, "Croston Classic for intermittent demand"),
    "croston_sba":     (score_croston_sba,     "classical-intermittent", False, "Croston SBA variant"),
    "seasonal_naive":  (score_seasonal_naive_week, "classical-baseline", False, "Value from N=7 draws ago"),
    "bayes_poisson":   (score_bayesian_poisson, "bayesian",        False, "Bayesian Poisson rate"),
    "neg_binomial":    (score_negative_binomial, "bayesian",       False, "Negative Binomial overdispersion"),
    "fourier":         (score_fourier_top_k,   "spectral",         False, "FFT top-K reconstruction"),
    "dmd":             (score_dmd_basic,       "spectral",         False, "Dynamic Mode Decomposition"),
}
