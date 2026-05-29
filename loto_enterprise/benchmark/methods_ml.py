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


def _sklearn_per_number(draws_2d, max_num, classifier_factory, lag: int = 10, context: int = 300) -> Dict[int, float]:
    """Train one classifier per number on lag features, predict next-step probability."""
    if not _check_sklearn():
        return {}
    if draws_2d.shape[0] < lag + 5:
        return {}
    binary = _build_binary(draws_2d, max_num)
    ctx = min(context, binary.shape[1])
    scores: Dict[int, float] = {}
    for i in range(max_num):
        s = binary[i, -ctx:].astype(np.int32)
        if s.sum() < 2 or s.sum() > len(s) - 2:
            # Degenerate (all zeros or all ones in window) — fallback to mean
            scores[i + 1] = float(s.mean())
            continue
        X, y = _build_lag_features(s, lag_window=lag)
        if X is None or len(np.unique(y)) < 2:
            scores[i + 1] = float(s.mean())
            continue
        try:
            clf = classifier_factory()
            clf.fit(X, y)
            # Predict for the most recent lag window
            x_pred = s[-lag:].reshape(1, -1)
            if hasattr(clf, "predict_proba"):
                p = float(clf.predict_proba(x_pred)[0, 1]) if clf.predict_proba(x_pred).shape[1] > 1 else 0.5
            else:
                p = float(clf.predict(x_pred)[0])
            scores[i + 1] = max(0.0, min(1.0, p))
        except Exception:
            scores[i + 1] = float(s.mean())
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

ML_METHODS: Dict[str, Tuple[Callable, str, bool, str]] = {
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
