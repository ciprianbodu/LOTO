"""Classical statistical + Markov + Bayesian + spectral prediction methods.

Each scorer respects the same interface as methods.py:
    score_xxx(draws_2d: np.ndarray, max_num: int) -> Dict[int, float]

All methods here are CPU-friendly (no GPU required). Lazy imports — if a
library is missing, the scorer returns {} and the method is marked unavailable.
"""
from __future__ import annotations

import logging
import warnings
from typing import Dict, List, Optional, Tuple, Callable

import numpy as np

warnings.filterwarnings("ignore")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared helpers (copy of utilities so this module is self-contained)
# ---------------------------------------------------------------------------

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

_STATSF_OK: Optional[bool] = None
_STATSF_ERR: Optional[str] = None


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


def _statsforecast_per_number(draws_2d, max_num, model_factory, context: int = 256) -> Dict[int, float]:
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
    scores: Dict[int, float] = {}
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


def score_arima_auto(draws_2d, max_num):
    if not _check_statsforecast():
        return {}
    try:
        from statsforecast.models import AutoARIMA
        return _statsforecast_per_number(draws_2d, max_num, lambda: AutoARIMA(season_length=1, max_p=2, max_q=2))
    except Exception as exc:
        logger.debug(f"[arima_auto] {exc}")
        return {}


def score_ets_auto(draws_2d, max_num):
    if not _check_statsforecast():
        return {}
    try:
        from statsforecast.models import AutoETS
        return _statsforecast_per_number(draws_2d, max_num, lambda: AutoETS(season_length=1))
    except Exception as exc:
        logger.debug(f"[ets_auto] {exc}")
        return {}


def score_theta_auto(draws_2d, max_num):
    if not _check_statsforecast():
        return {}
    try:
        from statsforecast.models import AutoTheta
        return _statsforecast_per_number(draws_2d, max_num, lambda: AutoTheta(season_length=1))
    except Exception as exc:
        logger.debug(f"[theta_auto] {exc}")
        return {}


def score_ces_auto(draws_2d, max_num):
    if not _check_statsforecast():
        return {}
    try:
        from statsforecast.models import AutoCES
        return _statsforecast_per_number(draws_2d, max_num, lambda: AutoCES(season_length=1))
    except Exception as exc:
        logger.debug(f"[ces_auto] {exc}")
        return {}


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


def score_croston_optimized(draws_2d, max_num):
    if not _check_statsforecast():
        return {}
    try:
        from statsforecast.models import CrostonOptimized
        return _statsforecast_per_number(draws_2d, max_num, lambda: CrostonOptimized())
    except Exception as exc:
        logger.debug(f"[croston_optimized] {exc}")
        return {}


def score_tsb(draws_2d, max_num):
    if not _check_statsforecast():
        return {}
    try:
        from statsforecast.models import TSB
        return _statsforecast_per_number(draws_2d, max_num, lambda: TSB(alpha_d=0.1, alpha_p=0.1))
    except Exception as exc:
        logger.debug(f"[tsb] {exc}")
        return {}


def score_adida(draws_2d, max_num):
    if not _check_statsforecast():
        return {}
    try:
        from statsforecast.models import ADIDA
        return _statsforecast_per_number(draws_2d, max_num, lambda: ADIDA())
    except Exception as exc:
        logger.debug(f"[adida] {exc}")
        return {}


def score_imapa(draws_2d, max_num):
    if not _check_statsforecast():
        return {}
    try:
        from statsforecast.models import IMAPA
        return _statsforecast_per_number(draws_2d, max_num, lambda: IMAPA())
    except Exception as exc:
        logger.debug(f"[imapa] {exc}")
        return {}


def score_ses(draws_2d, max_num):
    if not _check_statsforecast():
        return {}
    try:
        from statsforecast.models import SimpleExponentialSmoothing
        return _statsforecast_per_number(draws_2d, max_num, lambda: SimpleExponentialSmoothing(alpha=0.3))
    except Exception as exc:
        logger.debug(f"[ses] {exc}")
        return {}


def score_naive(draws_2d, max_num):
    """Naive: last known value (binary 0/1) — captures very-short persistence."""
    binary = _build_binary(draws_2d, max_num)
    if binary.shape[1] == 0:
        return _normalize({n: 0.5 for n in range(1, max_num + 1)}, max_num)
    last = binary[:, -1]
    return _normalize({i + 1: float(last[i]) for i in range(max_num)}, max_num)


def score_seasonal_naive_week(draws_2d, max_num):
    """Lottery extracts often weekly — value from N steps ago (default 1 week ≈ 1 draw)."""
    binary = _build_binary(draws_2d, max_num)
    n = binary.shape[1]
    if n < 2:
        return _normalize({n: 0.5 for n in range(1, max_num + 1)}, max_num)
    lag = min(7, n - 1)
    return _normalize({i + 1: float(binary[i, -lag]) for i in range(max_num)}, max_num)


def score_drift(draws_2d, max_num):
    """Linear extrapolation: avg + slope * 1."""
    binary = _build_binary(draws_2d, max_num)
    n = binary.shape[1]
    if n < 3:
        return _normalize({n: 0.5 for n in range(1, max_num + 1)}, max_num)
    scores: Dict[int, float] = {}
    for i in range(max_num):
        s = binary[i, -min(50, n):]
        if len(s) < 2:
            scores[i + 1] = float(s.mean())
            continue
        xs = np.arange(len(s))
        slope = float(np.polyfit(xs, s, 1)[0])
        scores[i + 1] = float(s.mean() + slope * 1.0)
    return _normalize(scores, max_num)


# ===========================================================================
# MARKOV CHAINS & N-GRAMS — sequence models
# ===========================================================================

def _markov_score(draws_2d, max_num, order: int = 1, decay: float = 0.05) -> Dict[int, float]:
    """K-th order Markov chain on binary appearance: P(num appears | last K draws)."""
    if draws_2d.shape[0] <= order:
        return {}
    binary = _build_binary(draws_2d, max_num)  # (max_num, n)
    n = binary.shape[1]
    # For each number, conditional P(appear_t | appear_t-1..t-order)
    scores: Dict[int, float] = {}
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


def score_markov_1(draws_2d, max_num):
    return _markov_score(draws_2d, max_num, order=1)


def score_markov_2(draws_2d, max_num):
    return _markov_score(draws_2d, max_num, order=2)


def score_markov_3(draws_2d, max_num):
    return _markov_score(draws_2d, max_num, order=3)


def score_ngram_bigram(draws_2d, max_num):
    """Laplace-smoothed bigram on per-number binary sequence."""
    if draws_2d.shape[0] < 3:
        return {}
    binary = _build_binary(draws_2d, max_num)
    scores: Dict[int, float] = {}
    for i in range(max_num):
        s = binary[i].astype(int)
        # Count (prev → next) transitions: P(1 | last)
        last = s[-1]
        # transitions where prev=last
        prev = s[:-1]
        nxt = s[1:]
        mask = prev == last
        if mask.sum() == 0:
            scores[i + 1] = float(s.mean())
        else:
            count_1 = int(((nxt == 1) & mask).sum()) + 1  # Laplace
            count_total = int(mask.sum()) + 2
            scores[i + 1] = count_1 / count_total
    return _normalize(scores, max_num)


def score_ngram_trigram(draws_2d, max_num):
    """Laplace-smoothed trigram (last 2 values → next)."""
    if draws_2d.shape[0] < 4:
        return {}
    binary = _build_binary(draws_2d, max_num)
    scores: Dict[int, float] = {}
    for i in range(max_num):
        s = binary[i].astype(int)
        last2 = (int(s[-2]), int(s[-1]))
        # Find transitions matching last2 → next
        num = 1  # Laplace
        den = 2
        for t in range(len(s) - 2):
            if (int(s[t]), int(s[t + 1])) == last2:
                den += 1
                if s[t + 2] == 1:
                    num += 1
        scores[i + 1] = num / den
    return _normalize(scores, max_num)


def score_vlmm(draws_2d, max_num):
    """Variable-length Markov: try orders 1..4 and weighted combine by frequency."""
    if draws_2d.shape[0] < 5:
        return {}
    binary = _build_binary(draws_2d, max_num)
    n = binary.shape[1]
    scores: Dict[int, float] = {}
    for i in range(max_num):
        s = binary[i].astype(int)
        weighted_p = 0.0
        weight_sum = 0.0
        for order in (1, 2, 3, 4):
            if n < order + 2:
                continue
            state = tuple(int(x) for x in s[-order:])
            matches = 0
            hits = 0
            for t in range(n - order):
                if tuple(int(x) for x in s[t:t + order]) == state:
                    matches += 1
                    if s[t + order] == 1:
                        hits += 1
            if matches >= 2:
                p = (hits + 1) / (matches + 2)  # Laplace
                w = matches * order  # longer matches weight more
                weighted_p += p * w
                weight_sum += w
        scores[i + 1] = (weighted_p / weight_sum) if weight_sum > 0 else float(s.mean())
    return _normalize(scores, max_num)


# ===========================================================================
# BAYESIAN PRIORS
# ===========================================================================

def score_beta_binomial(draws_2d, max_num):
    """Beta-Binomial conjugate: prior Beta(α,β), posterior mean (α+k)/(α+β+n).
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
    scores: Dict[int, float] = {}
    for i in range(max_num):
        s = binary[i]
        eff_n = float(weights.sum() * len(s))
        eff_k = float((s * weights * len(s)).sum())
        scores[i + 1] = (alpha_0 + eff_k) / (alpha_0 + beta_0 + eff_n)
    return _normalize(scores, max_num)


def score_polya_urn(draws_2d, max_num):
    """Pólya urn: success reinforces P(success_next). Captures hot-streaks."""
    if draws_2d.shape[0] == 0:
        return {}
    binary = _build_binary(draws_2d, max_num)
    n = binary.shape[1]
    # Recency weight for "draws" near the end
    weights = np.exp(np.linspace(-1.5, 0.0, n))
    scores: Dict[int, float] = {}
    for i in range(max_num):
        s = binary[i]
        # Pólya: counts of successes vs failures, weighted by recency
        succ = float((s * weights).sum())
        fail = float(((1 - s) * weights).sum())
        scores[i + 1] = (succ + 1.0) / (succ + fail + 2.0)
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
    scores: Dict[int, float] = {}
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
    scores: Dict[int, float] = {}
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
    scores: Dict[int, float] = {}
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


def score_wavelet_haar(draws_2d, max_num):
    """Haar wavelet decomposition — extract trend from approximation coefficients."""
    binary = _build_binary(draws_2d, max_num)
    n = binary.shape[1]
    if n < 32:
        return {}
    scores: Dict[int, float] = {}
    for i in range(max_num):
        s = binary[i].astype(np.float32)
        # Single-level Haar: pairs of averages
        pad = (len(s) // 2) * 2
        s = s[:pad]
        approx = (s[::2] + s[1::2]) / 2.0
        # Trend = mean of last 10% of approx
        tail = approx[-max(2, len(approx) // 10):]
        scores[i + 1] = float(tail.mean())
    return _normalize(scores, max_num)


def score_stl_decompose(draws_2d, max_num):
    """STL decomposition: seasonal + trend + residual, predict trend+seasonal."""
    try:
        from statsmodels.tsa.seasonal import STL
    except Exception:
        return {}
    binary = _build_binary(draws_2d, max_num)
    n = binary.shape[1]
    if n < 30:
        return {}
    scores: Dict[int, float] = {}
    period = max(2, min(7, n // 5))
    for i in range(max_num):
        s = binary[i].astype(np.float64)
        if s.sum() < 3:
            scores[i + 1] = float(s.mean())
            continue
        try:
            stl = STL(s, period=period, robust=True).fit()
            # Predict: trend[-1] + seasonal[-period]
            seas = stl.seasonal[-period] if len(stl.seasonal) >= period else 0.0
            trend = stl.trend[-1] if not np.isnan(stl.trend[-1]) else float(s.mean())
            scores[i + 1] = float(trend + seas)
        except Exception:
            scores[i + 1] = float(s.mean())
    return _normalize(scores, max_num)


def score_ssa(draws_2d, max_num):
    """Singular Spectrum Analysis: extract trend via SVD of trajectory matrix."""
    binary = _build_binary(draws_2d, max_num)
    n = binary.shape[1]
    if n < 30:
        return {}
    L = min(20, n // 3)
    scores: Dict[int, float] = {}
    for i in range(max_num):
        s = binary[i].astype(np.float64)
        K = n - L + 1
        # Trajectory matrix
        X = np.column_stack([s[k:k + L] for k in range(K)])
        try:
            U, S, Vt = np.linalg.svd(X, full_matrices=False)
            # Reconstruct from top-1 component (trend)
            trend_matrix = (S[0] * U[:, :1]) @ Vt[:1, :]
            # Average antidiagonals to get series-shape reconstruction
            recon = np.zeros(n)
            counts = np.zeros(n)
            for a in range(L):
                for b in range(K):
                    recon[a + b] += trend_matrix[a, b]
                    counts[a + b] += 1
            recon /= np.maximum(counts, 1)
            scores[i + 1] = float(recon[-1])
        except Exception:
            scores[i + 1] = float(s.mean())
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
# HMM (Hidden Markov Model) — hmmlearn
# ===========================================================================

def score_hmm_gaussian(draws_2d, max_num):
    try:
        from hmmlearn import hmm
    except Exception:
        return {}
    binary = _build_binary(draws_2d, max_num)
    n = binary.shape[1]
    if n < 30:
        return {}
    scores: Dict[int, float] = {}
    for i in range(max_num):
        s = binary[i].astype(np.float64).reshape(-1, 1)
        if s.sum() < 3:
            scores[i + 1] = float(s.mean())
            continue
        try:
            model = hmm.GaussianHMM(n_components=2, covariance_type="diag", n_iter=10, random_state=42)
            model.fit(s)
            # Next state probabilities given last hidden state
            last_state = int(model.predict(s)[-1])
            trans = model.transmat_[last_state]
            # Expected next emission = sum(P(state) * mean[state])
            next_emission = float((trans @ model.means_.ravel()))
            scores[i + 1] = next_emission
        except Exception:
            scores[i + 1] = float(s.mean())
    return _normalize(scores, max_num)


# ===========================================================================
# Holt-Winters fallback (statsmodels)
# ===========================================================================

def _check_statsmodels() -> bool:
    try:
        import statsmodels  # noqa: F401
        return True
    except Exception:
        return False


def score_holtwinters_add(draws_2d, max_num):
    if not _check_statsmodels():
        return {}
    from statsmodels.tsa.holtwinters import ExponentialSmoothing
    binary = _build_binary(draws_2d, max_num)
    n = binary.shape[1]
    if n < 20:
        return {}
    scores: Dict[int, float] = {}
    for i in range(max_num):
        s = binary[i].astype(np.float64)
        if s.sum() < 3:
            scores[i + 1] = float(s.mean())
            continue
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                m = ExponentialSmoothing(s, trend="add", initialization_method="estimated").fit(disp=False)
            scores[i + 1] = float(m.forecast(1)[0])
        except Exception:
            scores[i + 1] = float(s.mean())
    return _normalize(scores, max_num)


def score_arima_statsmodels(draws_2d, max_num):
    """ARIMA(2,0,2) via statsmodels — alternative to AutoARIMA."""
    if not _check_statsmodels():
        return {}
    from statsmodels.tsa.arima.model import ARIMA
    binary = _build_binary(draws_2d, max_num)
    n = binary.shape[1]
    if n < 20:
        return {}
    scores: Dict[int, float] = {}
    for i in range(max_num):
        s = binary[i].astype(np.float64)
        if s.sum() < 3:
            scores[i + 1] = float(s.mean())
            continue
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                m = ARIMA(s, order=(2, 0, 2)).fit()
            scores[i + 1] = float(m.forecast(1)[0])
        except Exception:
            scores[i + 1] = float(s.mean())
    return _normalize(scores, max_num)


# ===========================================================================
# Registry of new classical methods
# ===========================================================================
# EXTRA matematice / geometrice (numpy pur, CPU, fără librării noi) 2026-05-31
# ===========================================================================

def score_gap_poisson(draws_2d, max_num):
    """Overdue prin model Poisson pe gap-uri: P(număr e 'datorat') ~ gap_curent/gap_mediu."""
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


def score_entropy_window(draws_2d, max_num):
    """Scor pe entropia aparițiilor: numere cu pattern regulat (entropie mică) → scor mai mare."""
    bm = _build_binary(draws_2d, max_num)
    n = bm.shape[1]
    w = min(50, n)
    scores = {}
    for i in range(max_num):
        seq = bm[i, -w:]
        p = float(seq.mean())
        if p <= 0 or p >= 1:
            ent = 0.0
        else:
            ent = -(p * np.log2(p) + (1 - p) * np.log2(1 - p))
        # regularitate (1-entropie) ponderată cu frecvența recentă
        scores[i + 1] = (1.0 - ent) * 0.5 + p * 0.5
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


def score_momentum(draws_2d, max_num):
    """Momentum hot/cold: rata recentă (15) minus rata pe termen lung (60) → numere în creștere."""
    bm = _build_binary(draws_2d, max_num)
    n = bm.shape[1]
    s_w, l_w = min(15, n), min(60, n)
    scores = {}
    for i in range(max_num):
        short = float(bm[i, -s_w:].mean()) if s_w else 0.0
        long = float(bm[i, -l_w:].mean()) if l_w else 0.0
        scores[i + 1] = short - long  # >0 = în creștere recentă
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


def score_centrality(draws_2d, max_num):
    """Centralitate în graful de co-apariții (degree weighted) — numere 'hub' care leagă multe altele."""
    n_draws = draws_2d.shape[0]
    if n_draws < 5:
        return {}
    deg = np.zeros(max_num + 1, dtype=np.float64)
    for row in draws_2d:
        nums = [int(v) for v in row if 1 <= int(v) <= max_num]
        for a in nums:
            deg[a] += len(nums) - 1
    scores = {k: float(deg[k]) for k in range(1, max_num + 1)}
    return _normalize(scores, max_num)


def score_runs_test(draws_2d, max_num):
    """Runs test: numere a căror serie de apariții se abate de la aleator (clustering/anti-clustering)."""
    bm = _build_binary(draws_2d, max_num)
    scores = {}
    for i in range(max_num):
        seq = bm[i].astype(int)
        n1 = int(seq.sum()); n0 = len(seq) - n1
        if n1 < 2 or n0 < 2:
            scores[i + 1] = 0.5
            continue
        runs = 1 + int((seq[1:] != seq[:-1]).sum())
        exp_runs = 1 + 2 * n1 * n0 / (n1 + n0)
        # abatere normalizată (clustering recent = potențial overdue)
        scores[i + 1] = abs(runs - exp_runs) / exp_runs
    return _normalize(scores, max_num)


def score_weighted_recent(draws_2d, max_num):
    """Frecvență cu decay liniar pe poziție + boost pe ultimele 5 extrageri (geometric-temporal)."""
    bm = _build_binary(draws_2d, max_num)
    n = bm.shape[1]
    weights = np.linspace(0.2, 1.0, n)  # recent = greutate mare
    scores = {}
    for i in range(max_num):
        base = float((bm[i] * weights).sum())
        boost = float(bm[i, -5:].sum()) * 2.0 if n >= 5 else 0.0
        scores[i + 1] = base + boost
    return _normalize(scores, max_num)


# ===========================================================================

CLASSICAL_METHODS: Dict[str, Tuple[Callable, str, bool, str]] = {
    # === EXTRA matematice/geometrice (numpy, CPU, fără instalări) 2026-05-31 ===
    "gap_poisson":     (score_gap_poisson,     "math-gap",       False, "Overdue Poisson pe gap-uri"),
    "entropy_window":  (score_entropy_window,  "math-entropy",   False, "Entropie aparitii (regularitate)"),
    "autocorr":        (score_autocorr,        "math-autocorr",  False, "Autocorelatie lag 1-5"),
    "momentum":        (score_momentum,        "math-momentum",  False, "Hot/cold momentum (15 vs 60)"),
    "pair_affinity":   (score_pair_affinity,   "geometric-graph", False, "Co-aparitie cu numerele recente"),
    "centrality":      (score_centrality,      "geometric-graph", False, "Centralitate graf co-aparitii"),
    "runs_test":       (score_runs_test,       "math-runs",      False, "Runs test (clustering serie)"),
    "weighted_recent": (score_weighted_recent, "math-temporal",  False, "Frecventa cu decay + boost recent"),
    # statsforecast family
    "arima_auto":      (score_arima_auto,      "classical-arima",  False, "AutoARIMA per-number (statsforecast)"),
    "ets_auto":        (score_ets_auto,        "classical-ets",    False, "AutoETS per-number"),
    "theta_auto":      (score_theta_auto,      "classical-theta",  False, "AutoTheta per-number"),
    "ces_auto":        (score_ces_auto,        "classical-ces",    False, "Complex Exponential Smoothing"),
    "croston_classic": (score_croston_classic, "classical-intermittent", False, "Croston Classic for intermittent demand"),
    "croston_sba":     (score_croston_sba,     "classical-intermittent", False, "Croston SBA variant"),
    "croston_opt":     (score_croston_optimized, "classical-intermittent", False, "Croston Optimized"),
    "tsb":             (score_tsb,             "classical-intermittent", False, "Teunter-Syntetos-Babai"),
    "adida":           (score_adida,           "classical-intermittent", False, "ADIDA aggregate-disaggregate"),
    "imapa":           (score_imapa,           "classical-intermittent", False, "IMAPA multi-aggregate"),
    "ses":             (score_ses,             "classical-smoothing", False, "Simple Exponential Smoothing α=0.3"),
    "naive_last":      (score_naive,           "classical-baseline", False, "Naive: last observed value"),
    "seasonal_naive":  (score_seasonal_naive_week, "classical-baseline", False, "Value from N=7 draws ago"),
    "drift":           (score_drift,           "classical-baseline", False, "Linear drift extrapolation"),
    # Markov & sequence
    "markov_1":        (score_markov_1,        "markov",           False, "First-order Markov chain on binary"),
    "markov_2":        (score_markov_2,        "markov",           False, "Second-order Markov chain"),
    "markov_3":        (score_markov_3,        "markov",           False, "Third-order Markov chain"),
    "ngram_bigram":    (score_ngram_bigram,    "markov",           False, "Laplace-smoothed bigram"),
    "ngram_trigram":   (score_ngram_trigram,   "markov",           False, "Laplace-smoothed trigram"),
    "vlmm":            (score_vlmm,            "markov",           False, "Variable-length Markov model"),
    # Bayesian
    "beta_binomial":   (score_beta_binomial,   "bayesian",         False, "Beta-Binomial conjugate posterior"),
    "polya_urn":       (score_polya_urn,       "bayesian",         False, "Pólya urn reinforcement"),
    "bayes_poisson":   (score_bayesian_poisson, "bayesian",        False, "Bayesian Poisson rate"),
    "neg_binomial":    (score_negative_binomial, "bayesian",       False, "Negative Binomial overdispersion"),
    # Spectral / decomposition
    "fourier":         (score_fourier_top_k,   "spectral",         False, "FFT top-K reconstruction"),
    "wavelet_haar":    (score_wavelet_haar,    "spectral",         False, "Haar wavelet approximation"),
    "stl":             (score_stl_decompose,   "spectral",         False, "STL seasonal-trend decomposition"),
    "ssa":             (score_ssa,             "spectral",         False, "Singular Spectrum Analysis"),
    "dmd":             (score_dmd_basic,       "spectral",         False, "Dynamic Mode Decomposition"),
    # HMM
    "hmm_gaussian":    (score_hmm_gaussian,    "hmm",              False, "Gaussian HMM 2-state"),
    # Holt-Winters / statsmodels ARIMA
    "holt_winters":    (score_holtwinters_add, "classical-smoothing", False, "Holt-Winters additive trend"),
    "arima_sm":        (score_arima_statsmodels, "classical-arima", False, "ARIMA(2,0,2) statsmodels"),
}
