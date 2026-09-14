"""CPU scorers restored from tombstone (2026-09-14).

No GPU/torch. Helpers that already exist are re-registered;
sklearn/statsforecast wrappers follow the live `_sklearn_per_number` /
`_statsforecast_per_number` contracts.
"""

from __future__ import annotations

import logging
from typing import Callable

import numpy as np

from .methods_classical import (
    _build_binary,
    _markov_score,
    _normalize,
    _statsforecast_per_number,
    score_beta_binomial,
    score_gap_poisson,
    score_markov_2,
)
from .methods_ml import _check_sklearn, _sklearn_per_number

logger = logging.getLogger(__name__)


def _ml(factory):
    def score(draws_2d, max_num):
        if not _check_sklearn():
            return {}
        return _sklearn_per_number(draws_2d, max_num, factory)

    return score


def _sf(factory):
    def score(draws_2d, max_num):
        try:
            return _statsforecast_per_number(draws_2d, max_num, factory)
        except Exception as exc:
            logger.debug("[%s] %s", factory, exc)
            return {}

    return score


def score_recency_reg(draws_2d, max_num):
    from .methods import score_recency

    return score_recency(draws_2d, max_num)


def score_markov_1(draws_2d, max_num):
    return _markov_score(draws_2d, max_num, order=1)


def score_markov_3(draws_2d, max_num):
    return _markov_score(draws_2d, max_num, order=3)


def score_naive_last(draws_2d, max_num):
    if draws_2d.shape[0] == 0:
        return {}
    last = {int(v) for v in draws_2d[-1] if 1 <= int(v) <= max_num}
    return _normalize({n: 1.0 if n in last else 0.0 for n in range(1, max_num + 1)}, max_num)


def score_weighted_recent(draws_2d, max_num):
    n = draws_2d.shape[0]
    if n == 0:
        return {}
    weights = np.exp(np.linspace(-5.0, 0.0, n))
    values = np.asarray(draws_2d).astype(np.int64).ravel()
    repeated = np.repeat(weights, draws_2d.shape[1])
    valid = (values >= 1) & (values <= max_num)
    scores = np.bincount(values[valid], weights=repeated[valid], minlength=max_num + 1)
    return _normalize({i: float(scores[i]) for i in range(1, max_num + 1)}, max_num)


def score_momentum(draws_2d, max_num):
    n = draws_2d.shape[0]
    if n < 20:
        return {}
    bm = _build_binary(draws_2d, max_num)
    short, long = bm[:, -15:].mean(axis=1), bm[:, -60:].mean(axis=1) if n >= 60 else bm.mean(axis=1)
    return _normalize({i + 1: float(short[i] - long[i]) for i in range(max_num)}, max_num)


def score_decade_balance(draws_2d, max_num):
    bm = _build_binary(draws_2d, max_num)
    rec = np.exp(np.linspace(-2.0, 0.0, bm.shape[1]))
    freq = (bm * rec[None, :]).sum(axis=1)
    width = max(10, int(np.ceil(max_num / 5)))
    band = (np.arange(max_num) // width).clip(0, 4)
    tot = np.array([freq[band == b].sum() + 1e-9 for b in range(5)])
    inv = 1.0 / tot
    return _normalize({i + 1: float(freq[i] * inv[band[i]]) for i in range(max_num)}, max_num)


def score_modular(draws_2d, max_num, mod: int = 7):
    bm = _build_binary(draws_2d, max_num)
    rec = np.exp(np.linspace(-2.0, 0.0, bm.shape[1]))
    freq = (bm * rec[None, :]).sum(axis=1)
    residue = np.arange(1, max_num + 1) % mod
    hot = np.array([freq[residue == r].sum() for r in range(mod)]) + 1e-9
    return _normalize({i + 1: float(freq[i] * hot[residue[i]]) for i in range(max_num)}, max_num)


def score_digit_root(draws_2d, max_num):
    def _root(n):
        while n > 9:
            n = sum(int(c) for c in str(n))
        return n

    bm = _build_binary(draws_2d, max_num)
    rec = np.exp(np.linspace(-2.0, 0.0, bm.shape[1]))
    freq = (bm * rec[None, :]).sum(axis=1)
    roots = np.array([_root(i + 1) for i in range(max_num)])
    hot = np.array([freq[roots == r].sum() for r in range(1, 10)]) + 1e-9
    return _normalize({i + 1: float(freq[i] * hot[roots[i] - 1]) for i in range(max_num)}, max_num)


def score_entropy_window(draws_2d, max_num, window: int = 40):
    if draws_2d.shape[0] < 10:
        return {}
    bm = _build_binary(draws_2d, max_num)[:, -window:]
    p = bm.mean(axis=1).clip(1e-6, 1 - 1e-6)
    ent = -(p * np.log(p) + (1 - p) * np.log(1 - p))
    return _normalize({i + 1: float(1.0 - ent[i]) for i in range(max_num)}, max_num)


def score_drift(draws_2d, max_num):
    n = draws_2d.shape[0]
    if n < 30:
        return {}
    bm = _build_binary(draws_2d, max_num)
    half = n // 2
    return _normalize(
        {i + 1: float(bm[i, half:].mean() - bm[i, :half].mean()) for i in range(max_num)},
        max_num,
    )


def score_spread_position(draws_2d, max_num):
    if draws_2d.shape[0] == 0:
        return {}
    last = np.asarray(draws_2d[-1], dtype=np.float64)
    last = last[(last >= 1) & (last <= max_num)]
    if last.size == 0:
        return {}
    mu = float(last.mean())
    return _normalize({n: -abs(n - mu) for n in range(1, max_num + 1)}, max_num)


def score_runs_test(draws_2d, max_num):
    bm = _build_binary(draws_2d, max_num)
    scores = {}
    for i in range(max_num):
        s = bm[i]
        changes = np.diff(s).astype(bool).sum() + 1
        scores[i + 1] = float(s[-1] / max(changes, 1))
    return _normalize(scores, max_num)


def score_polya_urn(draws_2d, max_num):
    counts = np.ones(max_num + 1, dtype=np.float64)
    for row in draws_2d:
        for v in row:
            vi = int(v)
            if 1 <= vi <= max_num:
                counts[vi] += 1.0
    return _normalize({i: float(counts[i]) for i in range(1, max_num + 1)}, max_num)


def score_ngram_bigram(draws_2d, max_num):
    return _markov_score(draws_2d, max_num, order=2)


def score_ngram_trigram(draws_2d, max_num):
    return _markov_score(draws_2d, max_num, order=3)


def score_ses(draws_2d, max_num, alpha: float = 0.3):
    bm = _build_binary(draws_2d, max_num)
    scores = {}
    for i in range(max_num):
        s = 0.0
        for x in bm[i]:
            s = alpha * float(x) + (1 - alpha) * s
        scores[i + 1] = s
    return _normalize(scores, max_num)


def score_wavelet_haar(draws_2d, max_num):
    bm = _build_binary(draws_2d, max_num)
    scores = {}
    for i in range(max_num):
        s = bm[i]
        if s.size < 4:
            scores[i + 1] = float(s.mean())
            continue
        even = s[0 : (s.size // 2) * 2 : 2]
        odd = s[1 : (s.size // 2) * 2 : 2]
        scores[i + 1] = float(np.abs(even - odd).mean())
    return _normalize(scores, max_num)


def score_compression(draws_2d, max_num):
    import zlib

    bm = _build_binary(draws_2d, max_num)
    scores = {}
    for i in range(max_num):
        raw = np.packbits(bm[i].astype(np.uint8)).tobytes()
        scores[i + 1] = float(len(zlib.compress(raw, 1)))
    return _normalize(scores, max_num)


def score_assoc_rules(draws_2d, max_num):
    if draws_2d.shape[0] < 5:
        return {}
    last = {int(v) for v in draws_2d[-1] if 1 <= int(v) <= max_num}
    co = np.zeros(max_num + 1, dtype=np.float64)
    for row in draws_2d[:-1]:
        nums = {int(v) for v in row if 1 <= int(v) <= max_num}
        if nums & last:
            for n in nums:
                co[n] += 1.0
    return _normalize({i: float(co[i]) for i in range(1, max_num + 1)}, max_num)


def score_centrality(draws_2d, max_num):
    from .methods_graph import score_graph_degree

    return score_graph_degree(draws_2d, max_num)


def _cover_leftover(draws_2d, max_num, mode: str):
    """Gain static pe extrageri, nu leftover greedy iterativ.

    ``uncovered`` rămâne 1 pe fiecare extragere: nu se actualizează după un
    „pick". În mode=greedy, ``gain`` e frecvența ponderată recency — rang identic
    cu ``frequency``. mode=min_overlap / rarity / entropy schimbă rangul prin
    divizor, nu prin leftover real. cover_diversity_mmr folosește min_overlap.
    """
    if draws_2d.shape[0] < 8:
        return {}
    uncovered = np.ones(draws_2d.shape[0], dtype=np.float64)
    rec = np.exp(np.linspace(-2.0, 0.0, draws_2d.shape[0]))
    bm = _build_binary(draws_2d, max_num)
    gain = (bm * (uncovered * rec)[None, :]).sum(axis=1)
    if mode == "rarity":
        freq = bm.sum(axis=1) + 1.0
        gain = gain / freq
    elif mode == "min_overlap":
        co = bm @ bm.T
        gain = gain / (co.sum(axis=1) + 1.0)
    elif mode == "entropy":
        p = bm.mean(axis=1).clip(1e-6, 1 - 1e-6)
        gain = gain * (-(p * np.log(p)))
    return _normalize({i + 1: float(gain[i]) for i in range(max_num)}, max_num)


def score_cover_greedy(draws_2d, max_num):
    return _cover_leftover(draws_2d, max_num, "greedy")


def score_cover_rarity(draws_2d, max_num):
    return _cover_leftover(draws_2d, max_num, "rarity")


def score_cover_min_overlap(draws_2d, max_num):
    return _cover_leftover(draws_2d, max_num, "min_overlap")


def score_cover_entropy_max(draws_2d, max_num):
    return _cover_leftover(draws_2d, max_num, "entropy")


def score_cover_complement(draws_2d, max_num):
    bm = _build_binary(draws_2d, max_num)
    last = bm[:, -1] if bm.shape[1] else np.zeros(max_num)
    rec = (bm * np.exp(np.linspace(-2.0, 0.0, bm.shape[1]))[None, :]).sum(axis=1)
    return _normalize({i + 1: float(rec[i] * (1.0 - last[i])) for i in range(max_num)}, max_num)


def score_cover_diversity_mmr(draws_2d, max_num):
    return _cover_leftover(draws_2d, max_num, "min_overlap")


def score_cover_harmonic_rank(draws_2d, max_num):
    from .methods import score_frequency
    from .methods import score_recency

    f = score_frequency(draws_2d, max_num)
    r = score_recency(draws_2d, max_num)
    return _normalize({n: 2.0 / (1.0 / (f[n] + 1e-9) + 1.0 / (r[n] + 1e-9)) for n in f}, max_num)


def score_cover_balanced_spread(draws_2d, max_num):
    return score_decade_balance(draws_2d, max_num)


def score_cover_adaptive_blend(draws_2d, max_num):
    a = score_cover_greedy(draws_2d, max_num)
    b = score_cover_rarity(draws_2d, max_num)
    if not a or not b:
        return a or b
    return _normalize({n: 0.5 * a[n] + 0.5 * b[n] for n in a}, max_num)


def score_cover_temporal_shift(draws_2d, max_num):
    return score_drift(draws_2d, max_num)


def score_cover_triplet(draws_2d, max_num):
    return score_assoc_rules(draws_2d, max_num)


def score_winslips(draws_2d, max_num):
    return score_cover_greedy(draws_2d, max_num)


def score_ssa(draws_2d, max_num):
    from .methods_math_extra import score_pca_resid_surprise

    return score_pca_resid_surprise(draws_2d, max_num)


def score_vlmm(draws_2d, max_num):
    return _markov_score(draws_2d, max_num, order=3)


def score_arima_sm(draws_2d, max_num):
    try:
        from statsmodels.tsa.arima.model import ARIMA
    except Exception:
        return {}
    bm = _build_binary(draws_2d, max_num)
    ctx = min(80, bm.shape[1])
    scores = {}
    for i in range(max_num):
        s = bm[i, -ctx:].astype(np.float64)
        if s.sum() < 2:
            scores[i + 1] = 0.0
            continue
        try:
            fit = ARIMA(s, order=(1, 0, 0)).fit(method_kwargs={"warn_convergence": False})
            scores[i + 1] = float(np.asarray(fit.forecast(1))[0])
        except Exception:
            scores[i + 1] = float(s.mean())
    return _normalize(scores, max_num)


def score_holt_winters(draws_2d, max_num):
    try:
        from statsmodels.tsa.holtwinters import ExponentialSmoothing
    except Exception:
        return {}
    bm = _build_binary(draws_2d, max_num)
    ctx = min(80, bm.shape[1])
    scores = {}
    for i in range(max_num):
        s = bm[i, -ctx:].astype(np.float64)
        if s.sum() < 2:
            scores[i + 1] = 0.0
            continue
        try:
            fit = ExponentialSmoothing(s, trend=None, seasonal=None).fit(optimized=True)
            scores[i + 1] = float(np.asarray(fit.forecast(1))[0])
        except Exception:
            scores[i + 1] = float(s.mean())
    return _normalize(scores, max_num)


def score_stl(draws_2d, max_num):
    try:
        from statsmodels.tsa.seasonal import STL
    except Exception:
        return {}
    if draws_2d.shape[0] < 20:
        return {}
    bm = _build_binary(draws_2d, max_num)
    scores = {}
    period = 7 if bm.shape[1] >= 14 else max(3, bm.shape[1] // 4)
    for i in range(max_num):
        s = bm[i].astype(np.float64)
        try:
            res = STL(s, period=period, robust=True).fit()
            scores[i + 1] = float(res.trend[-1] + res.seasonal[-1])
        except Exception:
            scores[i + 1] = float(s.mean())
    return _normalize(scores, max_num)


def score_hmm_gaussian(draws_2d, max_num):
    try:
        from hmmlearn.hmm import GaussianHMM
    except Exception:
        return {}
    if draws_2d.shape[0] < 30:
        return {}
    bm = _build_binary(draws_2d, max_num)
    scores = {}
    for i in range(max_num):
        x = bm[i].reshape(-1, 1)
        try:
            m = GaussianHMM(n_components=2, covariance_type="diag", n_iter=20, random_state=0)
            m.fit(x)
            scores[i + 1] = float(m.predict_proba(x[-1:])[0, 1])
        except Exception:
            scores[i + 1] = float(x.mean())
    return _normalize(scores, max_num)


def _lazy_sf(import_name, cls_name):
    def factory():
        mod = __import__("statsforecast.models", fromlist=[cls_name])
        return getattr(mod, cls_name)()

    factory.__name__ = import_name
    return _sf(factory)


def _lazy_ml(builder):
    def factory():
        return builder()

    return _ml(factory)


def _rf():
    from sklearn.ensemble import RandomForestClassifier

    return RandomForestClassifier(n_estimators=40, max_depth=6, random_state=42, n_jobs=1)


def _et():
    from sklearn.ensemble import ExtraTreesClassifier

    return ExtraTreesClassifier(n_estimators=40, max_depth=6, random_state=42, n_jobs=1)


def _et_deep():
    from sklearn.ensemble import ExtraTreesClassifier

    return ExtraTreesClassifier(n_estimators=60, max_depth=10, random_state=42, n_jobs=1)


def _gb():
    from sklearn.ensemble import GradientBoostingClassifier

    return GradientBoostingClassifier(n_estimators=30, max_depth=3, random_state=42)


def _hgb():
    from sklearn.ensemble import HistGradientBoostingClassifier

    return HistGradientBoostingClassifier(max_depth=4, max_iter=40, random_state=42)


def _hgb_deep():
    from sklearn.ensemble import HistGradientBoostingClassifier

    return HistGradientBoostingClassifier(max_depth=8, max_iter=60, random_state=42)


def _ada():
    from sklearn.ensemble import AdaBoostClassifier

    return AdaBoostClassifier(n_estimators=30, random_state=42)


def _bag():
    from sklearn.ensemble import BaggingClassifier

    return BaggingClassifier(n_estimators=20, random_state=42, n_jobs=1)


def _knn(k):
    def factory():
        from sklearn.neighbors import KNeighborsClassifier

        return KNeighborsClassifier(n_neighbors=k)

    return factory


def _mlp(deep=False):
    def factory():
        from sklearn.neural_network import MLPClassifier

        hidden = (32, 16) if deep else (16,)
        return MLPClassifier(hidden_layer_sizes=hidden, max_iter=120, random_state=42)

    return factory


def _nb():
    from sklearn.naive_bayes import GaussianNB

    return GaussianNB()


def _bnb():
    from sklearn.naive_bayes import BernoulliNB

    return BernoulliNB()


def _perc():
    from sklearn.linear_model import Perceptron

    return Perceptron(max_iter=200, random_state=42)


def _poly():
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import PolynomialFeatures

    return make_pipeline(PolynomialFeatures(2, include_bias=False), LogisticRegression(max_iter=120))


def _qda():
    from sklearn.discriminant_analysis import QuadraticDiscriminantAnalysis

    return QuadraticDiscriminantAnalysis()


def _sgd():
    from sklearn.linear_model import SGDClassifier

    return SGDClassifier(loss="log_loss", max_iter=200, random_state=42)


def _svm_lin():
    from sklearn.svm import LinearSVC

    return LinearSVC(random_state=42, max_iter=400)


def _xgb():
    from xgboost import XGBClassifier

    return XGBClassifier(n_estimators=40, max_depth=3, n_jobs=1, eval_metric="logloss")


def _lgbm():
    from lightgbm import LGBMClassifier

    return LGBMClassifier(n_estimators=40, max_depth=4, verbose=-1, n_jobs=1)


def _vote():
    from sklearn.ensemble import VotingClassifier
    from sklearn.linear_model import LogisticRegression, RidgeClassifier

    return VotingClassifier(
        estimators=[
            ("lr", LogisticRegression(max_iter=120)),
            ("ridge", RidgeClassifier()),
        ],
        voting="hard",
    )


REVIVED_METHODS: dict[str, tuple[Callable, str, bool, str]] = {
    "recency": (score_recency_reg, "baseline", False, "Gap since last appearance"),
    "markov_1": (score_markov_1, "markov", False, "Order-1 Markov on binary series"),
    "markov_2": (score_markov_2, "markov", False, "Order-2 Markov on binary series"),
    "markov_3": (score_markov_3, "markov", False, "Order-3 Markov on binary series"),
    "beta_binomial": (score_beta_binomial, "bayesian", False, "Beta-Binomial posterior mean"),
    "gap_poisson": (score_gap_poisson, "classical-gap", False, "Overdue vs mean gap"),
    "naive_last": (score_naive_last, "classical-baseline", False, "Last-draw indicator"),
    "weighted_recent": (score_weighted_recent, "classical-baseline", False, "Steeper recency weights"),
    "momentum": (score_momentum, "classical-trend", False, "Short minus long frequency"),
    "decade_balance": (score_decade_balance, "geometric", False, "Decade-band inverse weight"),
    "modular": (score_modular, "number-theory", False, "Mod-7 class heat"),
    "digit_root": (score_digit_root, "number-theory", False, "Digital-root class heat"),
    "entropy_window": (score_entropy_window, "info", False, "Low binary entropy"),
    "drift": (score_drift, "classical-trend", False, "Recent minus early frequency"),
    "spread_position": (score_spread_position, "geometric", False, "Near last-draw mean"),
    "runs_test": (score_runs_test, "classical-gap", False, "Current run / switches"),
    "polya_urn": (score_polya_urn, "bayesian", False, "Add-one urn counts"),
    "ngram_bigram": (score_ngram_bigram, "markov", False, "Alias markov-2"),
    "ngram_trigram": (score_ngram_trigram, "markov", False, "Alias markov-3"),
    "ses": (score_ses, "classical-smoothing", False, "Simple exponential smoothing"),
    "wavelet_haar": (score_wavelet_haar, "spectral", False, "Haar detail energy"),
    "compression": (score_compression, "info", False, "zlib length of indicator"),
    "assoc_rules": (score_assoc_rules, "geometric-graph", False, "Co-occur with last draw"),
    "centrality": (score_centrality, "graph/network (numpy)", False, "Co-occurrence degree"),
    "cover_greedy": (score_cover_greedy, "coverage", False, "Uncovered-draw gain"),
    "cover_rarity": (score_cover_rarity, "coverage", False, "Gain / frequency"),
    "cover_min_overlap": (score_cover_min_overlap, "coverage", False, "Gain / co-occurrence"),
    "cover_entropy_max": (score_cover_entropy_max, "coverage", False, "Gain × entropy"),
    "cover_complement": (score_cover_complement, "coverage", False, "Recent freq off last draw"),
    "cover_diversity_mmr": (score_cover_diversity_mmr, "coverage", False, "MMR via min overlap"),
    "cover_harmonic_rank": (score_cover_harmonic_rank, "coverage", False, "Harmonic freq+recency"),
    "cover_balanced_spread": (score_cover_balanced_spread, "coverage", False, "Decade balance"),
    "cover_adaptive_blend": (score_cover_adaptive_blend, "coverage", False, "Greedy+rarity blend"),
    "cover_temporal_shift": (score_cover_temporal_shift, "coverage", False, "Alias drift"),
    "cover_triplet": (score_cover_triplet, "coverage", False, "Alias assoc rules"),
    "winslips": (score_winslips, "coverage", False, "Alias cover_greedy"),
    "ssa": (score_ssa, "spectral", False, "PCA residual surprise"),
    "vlmm": (score_vlmm, "markov", False, "Alias markov-3"),
    "arima_sm": (score_arima_sm, "classical-arima", True, "statsmodels ARIMA(1,0,0)"),
    "holt_winters": (score_holt_winters, "classical-smoothing", True, "Holt-Winters level"),
    "stl": (score_stl, "classical-decomp", True, "STL trend+season"),
    "hmm_gaussian": (score_hmm_gaussian, "classical-hmm", True, "2-state Gaussian HMM"),
    "arima_auto": (_lazy_sf("arima_auto", "AutoARIMA"), "classical-arima", True, "statsforecast AutoARIMA"),
    "ets_auto": (_lazy_sf("ets_auto", "AutoETS"), "classical-smoothing", True, "statsforecast AutoETS"),
    "ces_auto": (_lazy_sf("ces_auto", "AutoCES"), "classical-smoothing", True, "statsforecast AutoCES"),
    "theta_auto": (_lazy_sf("theta_auto", "AutoTheta"), "classical-smoothing", True, "statsforecast AutoTheta"),
    "adida": (_lazy_sf("adida", "ADIDA"), "classical-intermittent", True, "statsforecast ADIDA"),
    "imapa": (_lazy_sf("imapa", "IMAPA"), "classical-intermittent", True, "statsforecast IMAPA"),
    "tsb": (_lazy_sf("tsb", "TSB"), "classical-intermittent", True, "statsforecast TSB"),
    "croston_opt": (_lazy_sf("croston_opt", "CrostonOptimized"), "classical-intermittent", True, "Optimized Croston"),
    "ml_rf": (_lazy_ml(_rf), "ml-tree", True, "Random Forest"),
    "ml_extra_trees": (_lazy_ml(_et), "ml-tree", True, "Extra Trees"),
    "ml_extra_trees_deep": (_lazy_ml(_et_deep), "ml-tree", True, "Extra Trees deeper"),
    "ml_gradient_boost": (_lazy_ml(_gb), "ml-boost", True, "Gradient Boosting"),
    "ml_hist_gb": (_lazy_ml(_hgb), "ml-boost", True, "HistGradientBoosting"),
    "ml_hist_gb_deep": (_lazy_ml(_hgb_deep), "ml-boost", True, "HistGB deeper"),
    "ml_adaboost": (_lazy_ml(_ada), "ml-boost", True, "AdaBoost"),
    "ml_bagging": (_lazy_ml(_bag), "ml-tree", True, "Bagging"),
    "ml_knn_15": (_lazy_ml(_knn(15)), "ml-knn", True, "KNN k=15"),
    "ml_knn_30": (_lazy_ml(_knn(30)), "ml-knn", True, "KNN k=30"),
    "ml_mlp": (_lazy_ml(_mlp(False)), "ml-nn", True, "MLP shallow"),
    "ml_mlp_deep": (_lazy_ml(_mlp(True)), "ml-nn", True, "MLP deeper"),
    "ml_naive_bayes": (_lazy_ml(_nb), "ml-bayes", True, "Gaussian NB"),
    "ml_bernoulli_nb": (_lazy_ml(_bnb), "ml-bayes", True, "Bernoulli NB"),
    "ml_perceptron": (_lazy_ml(_perc), "ml-linear", True, "Perceptron"),
    "ml_poly_logistic": (_lazy_ml(_poly), "ml-linear", True, "Poly+Logistic"),
    "ml_qda": (_lazy_ml(_qda), "ml-bayes", True, "QDA"),
    "ml_sgd_log": (_lazy_ml(_sgd), "ml-linear", True, "SGD log-loss"),
    "ml_svm_linear": (_lazy_ml(_svm_lin), "ml-kernel", True, "LinearSVC"),
    "ml_xgb": (_lazy_ml(_xgb), "ml-boost", True, "XGBoost CPU"),
    "ml_lgbm": (_lazy_ml(_lgbm), "ml-boost", True, "LightGBM CPU"),
    "ml_voting": (_lazy_ml(_vote), "ml-ensemble", True, "Hard vote LR+Ridge"),
}
