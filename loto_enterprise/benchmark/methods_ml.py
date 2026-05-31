"""Machine learning prediction methods (sklearn + boosting).

Each scorer respects the standard interface. ML methods use a windowed-lag
feature representation: for each candidate number, build features from the
last K binary appearance values and learn a 1-step-ahead classifier.

Boosting methods have CPU and GPU variants (suffix _gpu) — GPU variants
gracefully degrade to CPU if hardware/lib unavailable.
"""
from __future__ import annotations

import logging
import warnings
from typing import Dict, Tuple, Callable, Optional

import numpy as np

warnings.filterwarnings("ignore")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers (same as classical)
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
    n_draws = draws_2d.shape[0]
    bm = np.zeros((max_num, n_draws), dtype=np.float32)
    for i, row in enumerate(draws_2d):
        for v in row:
            vi = int(v)
            if 1 <= vi <= max_num:
                bm[vi - 1, i] = 1.0
    return bm


def _build_lag_features(series: np.ndarray, lag_window: int = 10):
    """Convert binary 1D series → (n-lag, lag) feature matrix + (n-lag,) target."""
    if len(series) <= lag_window:
        return None, None
    X = np.stack([series[i:i + lag_window] for i in range(len(series) - lag_window)], axis=0)
    y = series[lag_window:].astype(int)
    return X, y


# ---------------------------------------------------------------------------
# sklearn unified trainer
# ---------------------------------------------------------------------------

def _check_sklearn() -> bool:
    try:
        import sklearn  # noqa: F401
        return True
    except Exception:
        return False


def _score_one_number(s, lag, classifier_factory):
    """Antrenează 1 clasificator pe seria binară a unui număr → probabilitatea next-step.
    Extras ca funcție ca să poată rula PARALEL (joblib) pe toate nucleele."""
    if s.sum() < 2 or s.sum() > len(s) - 2:
        return float(s.mean())  # degenerat (tot 0 sau tot 1) → media
    X, y = _build_lag_features(s, lag_window=lag)
    if X is None or len(np.unique(y)) < 2:
        return float(s.mean())
    try:
        clf = classifier_factory()
        clf.fit(X, y)
        x_pred = s[-lag:].reshape(1, -1)
        if hasattr(clf, "predict_proba"):
            pp = clf.predict_proba(x_pred)
            p = float(pp[0, 1]) if pp.shape[1] > 1 else 0.5
        else:
            p = float(clf.predict(x_pred)[0])
        return max(0.0, min(1.0, p))
    except Exception:
        return float(s.mean())


def _sklearn_per_number(draws_2d, max_num, classifier_factory, lag: int = 10, context: int = 300) -> Dict[int, float]:
    """Antrenează un clasificator per număr, PARALEL pe toate nucleele (joblib).
    Înainte: 49 clasificatori în serie (1-2 threads). Acum: distribuiti pe toate
    nucleele → CPU saturat. Clasificatorul intern ramane n_jobs=1 (evita
    oversubscription: paralelism DOAR pe bucla numerelor, nu si in interior)."""
    if not _check_sklearn():
        return {}
    if draws_2d.shape[0] < lag + 5:
        return {}
    binary = _build_binary(draws_2d, max_num)
    ctx = min(context, binary.shape[1])
    series = [binary[i, -ctx:].astype(np.int32) for i in range(max_num)]
    # Cei 49 clasificatori rulează SECVENȚIAL aici, fiindcă paralelismul e acum la nivel
    # de METODĂ (runner.py rulează mai multe metode simultan pe nuclee). Dublu-paralelism
    # ar cauza oversubscription (N metode × 49 = mii de threads pe 32 nuclee → mai lent).
    results = [_score_one_number(s, lag, classifier_factory) for s in series]
    scores = {i + 1: float(results[i]) for i in range(max_num)}
    return _normalize(scores, max_num)


# ===========================================================================
# sklearn classifiers
# ===========================================================================

def score_ml_logistic(draws_2d, max_num):
    if not _check_sklearn():
        return {}
    from sklearn.linear_model import LogisticRegression
    return _sklearn_per_number(draws_2d, max_num, lambda: LogisticRegression(max_iter=200, C=1.0, solver="lbfgs"))


def score_ml_rf(draws_2d, max_num):
    if not _check_sklearn():
        return {}
    from sklearn.ensemble import RandomForestClassifier
    return _sklearn_per_number(draws_2d, max_num, lambda: RandomForestClassifier(n_estimators=50, max_depth=6, random_state=42, n_jobs=1))


def score_ml_extra_trees(draws_2d, max_num):
    if not _check_sklearn():
        return {}
    from sklearn.ensemble import ExtraTreesClassifier
    return _sklearn_per_number(draws_2d, max_num, lambda: ExtraTreesClassifier(n_estimators=50, max_depth=6, random_state=42, n_jobs=1))


def score_ml_gradient_boost(draws_2d, max_num):
    if not _check_sklearn():
        return {}
    from sklearn.ensemble import GradientBoostingClassifier
    return _sklearn_per_number(draws_2d, max_num, lambda: GradientBoostingClassifier(n_estimators=50, max_depth=3, random_state=42))


def score_ml_adaboost(draws_2d, max_num):
    if not _check_sklearn():
        return {}
    from sklearn.ensemble import AdaBoostClassifier
    return _sklearn_per_number(draws_2d, max_num, lambda: AdaBoostClassifier(n_estimators=50, random_state=42))


def score_ml_decision_tree(draws_2d, max_num):
    if not _check_sklearn():
        return {}
    from sklearn.tree import DecisionTreeClassifier
    return _sklearn_per_number(draws_2d, max_num, lambda: DecisionTreeClassifier(max_depth=6, random_state=42))


def score_ml_knn_5(draws_2d, max_num):
    if not _check_sklearn():
        return {}
    from sklearn.neighbors import KNeighborsClassifier
    return _sklearn_per_number(draws_2d, max_num, lambda: KNeighborsClassifier(n_neighbors=5))


def score_ml_knn_15(draws_2d, max_num):
    if not _check_sklearn():
        return {}
    from sklearn.neighbors import KNeighborsClassifier
    return _sklearn_per_number(draws_2d, max_num, lambda: KNeighborsClassifier(n_neighbors=15))


def score_ml_naive_bayes(draws_2d, max_num):
    if not _check_sklearn():
        return {}
    from sklearn.naive_bayes import GaussianNB
    return _sklearn_per_number(draws_2d, max_num, lambda: GaussianNB())


def score_ml_svm_rbf(draws_2d, max_num):
    if not _check_sklearn():
        return {}
    from sklearn.svm import SVC
    return _sklearn_per_number(draws_2d, max_num, lambda: SVC(kernel="rbf", probability=True, C=1.0))


def score_ml_lda(draws_2d, max_num):
    if not _check_sklearn():
        return {}
    from sklearn.discriminant_analysis import LinearDiscriminantAnalysis
    return _sklearn_per_number(draws_2d, max_num, lambda: LinearDiscriminantAnalysis())


def score_ml_qda(draws_2d, max_num):
    if not _check_sklearn():
        return {}
    from sklearn.discriminant_analysis import QuadraticDiscriminantAnalysis
    return _sklearn_per_number(draws_2d, max_num, lambda: QuadraticDiscriminantAnalysis())


def score_ml_ridge(draws_2d, max_num):
    if not _check_sklearn():
        return {}
    from sklearn.linear_model import RidgeClassifier
    return _sklearn_per_number(draws_2d, max_num, lambda: RidgeClassifier(alpha=1.0))


def score_ml_mlp(draws_2d, max_num):
    """Sklearn MLPClassifier — CPU only, small net."""
    if not _check_sklearn():
        return {}
    from sklearn.neural_network import MLPClassifier
    return _sklearn_per_number(draws_2d, max_num,
                               lambda: MLPClassifier(hidden_layer_sizes=(16, 8), max_iter=200, random_state=42))


# ===========================================================================
# Boosting — XGBoost / LightGBM / CatBoost (CPU + GPU variants)
# ===========================================================================

def _has_xgboost():
    try:
        import xgboost  # noqa: F401
        return True
    except Exception:
        return False


def _has_lightgbm():
    try:
        import lightgbm  # noqa: F401
        return True
    except Exception:
        return False


def _has_catboost():
    try:
        import catboost  # noqa: F401
        return True
    except Exception:
        return False


def _has_cuda():
    import os
    if os.environ.get("CUDA_VISIBLE_DEVICES") == "-1":
        return False
    try:
        import torch  # noqa: F401
        return bool(torch.cuda.is_available())
    except Exception:
        return False


def score_ml_xgb_cpu(draws_2d, max_num):
    if not _has_xgboost():
        return {}
    from xgboost import XGBClassifier
    return _sklearn_per_number(draws_2d, max_num,
                               lambda: XGBClassifier(n_estimators=50, max_depth=4, tree_method="hist",
                                                     verbosity=0, use_label_encoder=False, eval_metric="logloss"))


def score_ml_xgb_gpu(draws_2d, max_num):
    if not _has_xgboost():
        return {}
    if not _has_cuda():
        return {}  # Gracefully skip on CPU-only machines
    from xgboost import XGBClassifier
    return _sklearn_per_number(draws_2d, max_num,
                               lambda: XGBClassifier(n_estimators=50, max_depth=4, tree_method="hist",
                                                     device="cuda", verbosity=0,
                                                     use_label_encoder=False, eval_metric="logloss"))


def score_ml_lgbm_cpu(draws_2d, max_num):
    if not _has_lightgbm():
        return {}
    from lightgbm import LGBMClassifier
    return _sklearn_per_number(draws_2d, max_num,
                               lambda: LGBMClassifier(n_estimators=50, max_depth=4, num_leaves=15,
                                                      verbosity=-1, force_row_wise=True))


def score_ml_lgbm_gpu(draws_2d, max_num):
    if not _has_lightgbm():
        return {}
    if not _has_cuda():
        return {}
    from lightgbm import LGBMClassifier
    try:
        return _sklearn_per_number(draws_2d, max_num,
                                   lambda: LGBMClassifier(n_estimators=50, max_depth=4, num_leaves=15,
                                                          device_type="gpu", verbosity=-1))
    except Exception:
        # LightGBM GPU requires special build; gracefully fail
        return {}


def score_ml_catboost_cpu(draws_2d, max_num):
    if not _has_catboost():
        return {}
    from catboost import CatBoostClassifier
    return _sklearn_per_number(draws_2d, max_num,
                               lambda: CatBoostClassifier(iterations=50, depth=4, verbose=False,
                                                          allow_writing_files=False))


def score_ml_catboost_gpu(draws_2d, max_num):
    if not _has_catboost():
        return {}
    if not _has_cuda():
        return {}
    from catboost import CatBoostClassifier
    return _sklearn_per_number(draws_2d, max_num,
                               lambda: CatBoostClassifier(iterations=50, depth=4, task_type="GPU",
                                                          verbose=False, allow_writing_files=False))


# ===========================================================================
# Registry
# ===========================================================================

def score_ml_hist_gb(draws_2d, max_num):
    if not _check_sklearn():
        return {}
    from sklearn.ensemble import HistGradientBoostingClassifier
    return _sklearn_per_number(draws_2d, max_num, lambda: HistGradientBoostingClassifier(max_iter=120, max_depth=4, random_state=42))


def score_ml_hist_gb_deep(draws_2d, max_num):
    if not _check_sklearn():
        return {}
    from sklearn.ensemble import HistGradientBoostingClassifier
    return _sklearn_per_number(draws_2d, max_num, lambda: HistGradientBoostingClassifier(max_iter=200, max_depth=8, l2_regularization=1.0, random_state=42))


def score_ml_bernoulli_nb(draws_2d, max_num):
    if not _check_sklearn():
        return {}
    from sklearn.naive_bayes import BernoulliNB
    return _sklearn_per_number(draws_2d, max_num, lambda: BernoulliNB())


def score_ml_complement_nb(draws_2d, max_num):
    if not _check_sklearn():
        return {}
    from sklearn.naive_bayes import ComplementNB
    return _sklearn_per_number(draws_2d, max_num, lambda: ComplementNB())


def score_ml_sgd_log(draws_2d, max_num):
    if not _check_sklearn():
        return {}
    from sklearn.linear_model import SGDClassifier
    return _sklearn_per_number(draws_2d, max_num, lambda: SGDClassifier(loss="log_loss", max_iter=400, random_state=42))


def score_ml_passive_aggressive(draws_2d, max_num):
    if not _check_sklearn():
        return {}
    from sklearn.linear_model import PassiveAggressiveClassifier
    return _sklearn_per_number(draws_2d, max_num, lambda: PassiveAggressiveClassifier(max_iter=400, random_state=42))


def score_ml_perceptron(draws_2d, max_num):
    if not _check_sklearn():
        return {}
    from sklearn.linear_model import Perceptron
    return _sklearn_per_number(draws_2d, max_num, lambda: Perceptron(max_iter=400, random_state=42))


def score_ml_ridge_cv(draws_2d, max_num):
    if not _check_sklearn():
        return {}
    from sklearn.linear_model import RidgeClassifierCV
    return _sklearn_per_number(draws_2d, max_num, lambda: RidgeClassifierCV())


def score_ml_nearest_centroid(draws_2d, max_num):
    if not _check_sklearn():
        return {}
    from sklearn.neighbors import NearestCentroid
    return _sklearn_per_number(draws_2d, max_num, lambda: NearestCentroid())


def score_ml_knn_30(draws_2d, max_num):
    if not _check_sklearn():
        return {}
    from sklearn.neighbors import KNeighborsClassifier
    return _sklearn_per_number(draws_2d, max_num, lambda: KNeighborsClassifier(n_neighbors=30))


def score_ml_bagging(draws_2d, max_num):
    if not _check_sklearn():
        return {}
    from sklearn.ensemble import BaggingClassifier
    return _sklearn_per_number(draws_2d, max_num, lambda: BaggingClassifier(n_estimators=30, random_state=42, n_jobs=1))


def score_ml_extra_trees_deep(draws_2d, max_num):
    if not _check_sklearn():
        return {}
    from sklearn.ensemble import ExtraTreesClassifier
    return _sklearn_per_number(draws_2d, max_num, lambda: ExtraTreesClassifier(n_estimators=120, max_depth=12, random_state=42, n_jobs=1))


def score_ml_mlp_deep(draws_2d, max_num):
    if not _check_sklearn():
        return {}
    from sklearn.neural_network import MLPClassifier
    return _sklearn_per_number(draws_2d, max_num, lambda: MLPClassifier(hidden_layer_sizes=(64, 32), max_iter=400, random_state=42))


def score_ml_svm_linear(draws_2d, max_num):
    if not _check_sklearn():
        return {}
    from sklearn.svm import SVC
    return _sklearn_per_number(draws_2d, max_num, lambda: SVC(kernel="linear", probability=True, random_state=42))


def score_ml_voting(draws_2d, max_num):
    if not _check_sklearn():
        return {}
    from sklearn.ensemble import VotingClassifier, HistGradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.naive_bayes import BernoulliNB

    def _mk():
        return VotingClassifier(
            estimators=[
                ("lr", LogisticRegression(max_iter=200)),
                ("nb", BernoulliNB()),
                ("hb", HistGradientBoostingClassifier(max_iter=80, max_depth=4, random_state=42)),
            ],
            voting="soft",
        )
    return _sklearn_per_number(draws_2d, max_num, _mk)


def score_ml_gaussian_process(draws_2d, max_num):
    """Gaussian Process classifier — modelare probabilista a incertitudinii pe lag-uri."""
    if not _check_sklearn():
        return {}
    from sklearn.gaussian_process import GaussianProcessClassifier
    from sklearn.gaussian_process.kernels import RBF
    # context mic (GP e O(n^3)) — limitam puternic
    return _sklearn_per_number(draws_2d, max_num,
                               lambda: GaussianProcessClassifier(kernel=1.0 * RBF(1.0), max_iter_predict=50),
                               lag=8, context=120)


def score_ml_poly_logistic(draws_2d, max_num):
    """Regresie logistica cu features polinomiale (grad 2) — capteaza interactiuni
    intre lag-uri (symbolic-lite: cauta relatii ne-liniare in date)."""
    if not _check_sklearn():
        return {}
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import PolynomialFeatures
    from sklearn.pipeline import make_pipeline
    return _sklearn_per_number(draws_2d, max_num,
                               lambda: make_pipeline(
                                   PolynomialFeatures(degree=2, include_bias=False),
                                   LogisticRegression(max_iter=300, C=0.5)),
                               lag=6)


ML_METHODS: Dict[str, Tuple[Callable, str, bool, str]] = {
    # === Nisa: Gaussian Process + polinomial (sklearn, CPU) 2026-05-31 ===
    "ml_gaussian_process": (score_ml_gaussian_process, "ml-kernel", True, "Gaussian Process (incertitudine probabilista)"),
    "ml_poly_logistic":    (score_ml_poly_logistic,    "ml-linear", True, "Logistic + features polinomiale grad 2"),
    # === EXTRA sklearn (gratuite, Python 3.11, fără instalări) 2026-05-30 ===
    "ml_hist_gb":          (score_ml_hist_gb,          "ml-boost",  True, "HistGradientBoosting (sklearn, rapid)"),
    "ml_hist_gb_deep":     (score_ml_hist_gb_deep,     "ml-boost",  True, "HistGradientBoosting adânc + L2"),
    "ml_bernoulli_nb":     (score_ml_bernoulli_nb,     "ml-bayes",  True, "Bernoulli Naive Bayes (binar)"),
    "ml_complement_nb":    (score_ml_complement_nb,    "ml-bayes",  True, "Complement Naive Bayes"),
    "ml_sgd_log":          (score_ml_sgd_log,          "ml-linear", True, "SGD log-loss (regresie logistică online)"),
    "ml_passive_aggressive": (score_ml_passive_aggressive, "ml-linear", True, "Passive-Aggressive"),
    "ml_perceptron":       (score_ml_perceptron,       "ml-linear", True, "Perceptron"),
    "ml_ridge_cv":         (score_ml_ridge_cv,         "ml-linear", True, "Ridge Classifier CV"),
    "ml_nearest_centroid": (score_ml_nearest_centroid, "ml-knn",    True, "Nearest Centroid"),
    "ml_knn_30":           (score_ml_knn_30,           "ml-knn",    True, "K-NN k=30"),
    "ml_bagging":          (score_ml_bagging,          "ml-tree",   True, "Bagging (30 estimatori)"),
    "ml_extra_trees_deep": (score_ml_extra_trees_deep, "ml-tree",   True, "Extra Trees adânc (120)"),
    "ml_mlp_deep":         (score_ml_mlp_deep,         "ml-nn",     True, "MLP (64,32) sklearn"),
    "ml_svm_linear":       (score_ml_svm_linear,       "ml-kernel", True, "SVM kernel liniar"),
    "ml_voting":           (score_ml_voting,           "ml-ensemble", True, "Voting soft (logistic+BNB+HistGB)"),
    # sklearn (all CPU)
    "ml_logistic":      (score_ml_logistic,      "ml-linear",   True,  "Logistic Regression (sklearn)"),
    "ml_ridge":         (score_ml_ridge,         "ml-linear",   True,  "Ridge Classifier"),
    "ml_lda":           (score_ml_lda,           "ml-linear",   True,  "Linear Discriminant Analysis"),
    "ml_qda":           (score_ml_qda,           "ml-linear",   True,  "Quadratic Discriminant Analysis"),
    "ml_naive_bayes":   (score_ml_naive_bayes,   "ml-bayes",    True,  "Gaussian Naive Bayes"),
    "ml_decision_tree": (score_ml_decision_tree, "ml-tree",     True,  "Decision Tree"),
    "ml_rf":            (score_ml_rf,            "ml-tree",     True,  "Random Forest (50 trees)"),
    "ml_extra_trees":   (score_ml_extra_trees,   "ml-tree",     True,  "Extra Trees (50 trees)"),
    "ml_gradient_boost": (score_ml_gradient_boost, "ml-boost",  True,  "Gradient Boosting (sklearn)"),
    "ml_adaboost":      (score_ml_adaboost,      "ml-boost",    True,  "AdaBoost (50 estimators)"),
    "ml_knn_5":         (score_ml_knn_5,         "ml-knn",      True,  "K-NN k=5"),
    "ml_knn_15":        (score_ml_knn_15,        "ml-knn",      True,  "K-NN k=15"),
    "ml_svm_rbf":       (score_ml_svm_rbf,       "ml-kernel",   True,  "SVM RBF kernel"),
    "ml_mlp":           (score_ml_mlp,           "ml-nn",       True,  "MLP (16,8) sklearn"),
    # Boosting CPU
    "ml_xgb_cpu":       (score_ml_xgb_cpu,       "ml-boost",    True,  "XGBoost CPU (hist)"),
    "ml_lgbm_cpu":      (score_ml_lgbm_cpu,      "ml-boost",    True,  "LightGBM CPU"),
    "ml_catboost_cpu":  (score_ml_catboost_cpu,  "ml-boost",    True,  "CatBoost CPU"),
    # Boosting GPU (LUPTATORI)
    "ml_xgb_gpu":       (score_ml_xgb_gpu,       "ml-boost-gpu", True, "XGBoost CUDA (LUPTATORI)"),
    "ml_lgbm_gpu":      (score_ml_lgbm_gpu,      "ml-boost-gpu", True, "LightGBM GPU"),
    "ml_catboost_gpu":  (score_ml_catboost_gpu,  "ml-boost-gpu", True, "CatBoost GPU"),
}
