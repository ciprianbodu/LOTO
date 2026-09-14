"""Machine learning prediction methods (sklearn + boosting).

Each scorer respects the standard interface. ML methods use a windowed-lag
feature representation: for each candidate number, build features from the
last K binary appearance values and learn a 1-step-ahead classifier.

Boosting methods are CPU-only (XGBoost / LightGBM / CatBoost). GPU variants au
fost eliminate odată cu tot suportul GPU din aplicație.
"""

from __future__ import annotations

import logging
import warnings
from typing import Callable

import numpy as np

warnings.filterwarnings("ignore")
logger = logging.getLogger(__name__)


def _normalize(scores: dict[int, float], max_num: int) -> dict[int, float]:
    if not scores:
        return {n: 0.0 for n in range(1, max_num + 1)}
    vals = np.fromiter((float(v) for v in scores.values()), dtype=np.float64)
    finite = vals[np.isfinite(vals)]
    if finite.size == 0:
        return {n: 0.0 for n in range(1, max_num + 1)}
    vmin, vmax = float(finite.min()), float(finite.max())
    rng = max(vmax - vmin, 1e-12)
    out = {
        int(k): float((float(v) - vmin) / rng) if np.isfinite(v) else 0.0
        for k, v in scores.items()
    }
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
    if len(series) <= lag_window:
        return None, None
    X = np.stack(
        [series[i : i + lag_window] for i in range(len(series) - lag_window)], axis=0
    )
    y = series[lag_window:].astype(int)
    return X, y


def _check_sklearn() -> bool:
    try:
        import sklearn  # noqa: F401

        return True
    except Exception:
        return False


def _score_one_number(s, lag, classifier_factory):
    if s.sum() < 2 or s.sum() > len(s) - 2:
        return float("nan")
    X, y = _build_lag_features(s, lag_window=lag)
    if X is None or len(np.unique(y)) < 2:
        return float("nan")
    try:
        clf = classifier_factory()
        clf.fit(X, y)
        x_pred = s[-lag:].reshape(1, -1)
        if hasattr(clf, "predict_proba"):
            pp = clf.predict_proba(x_pred)
            if pp.shape[1] > 1:
                return float(pp[0, 1])
            return 0.5
        if hasattr(clf, "decision_function"):
            df = np.asarray(clf.decision_function(x_pred), dtype=np.float64).reshape(-1)
            return float(df[0])
        return float(clf.predict(x_pred)[0])
    except Exception:
        return float("nan")


def _sklearn_per_number(
    draws_2d, max_num, classifier_factory, lag: int = 10, context: int = 300
) -> dict[int, float]:
    if not _check_sklearn():
        return {}
    if draws_2d.shape[0] < lag + 5:
        return {}
    binary = _build_binary(draws_2d, max_num)
    ctx = min(context, binary.shape[1])
    series = [binary[i, -ctx:].astype(np.int32) for i in range(max_num)]
    results = [_score_one_number(s, lag, classifier_factory) for s in series]
    valid = [r for r in results if r == r]
    if not valid:
        results = [float(s.mean()) for s in series]
    else:
        floor = min(valid)
        results = [r if r == r else floor for r in results]
    scores = {i + 1: float(results[i]) for i in range(max_num)}
    return _normalize(scores, max_num)


def score_ml_logistic(draws_2d, max_num):
    if not _check_sklearn():
        return {}
    from sklearn.linear_model import LogisticRegression

    return _sklearn_per_number(
        draws_2d,
        max_num,
        lambda: LogisticRegression(max_iter=200, C=1.0, solver="lbfgs"),
    )


def score_ml_decision_tree(draws_2d, max_num):
    if not _check_sklearn():
        return {}
    from sklearn.tree import DecisionTreeClassifier

    return _sklearn_per_number(
        draws_2d, max_num, lambda: DecisionTreeClassifier(max_depth=6, random_state=42)
    )


def score_ml_knn_5(draws_2d, max_num):
    if not _check_sklearn():
        return {}
    from sklearn.neighbors import KNeighborsClassifier

    return _sklearn_per_number(
        draws_2d, max_num, lambda: KNeighborsClassifier(n_neighbors=5)
    )


def score_ml_svm_rbf(draws_2d, max_num):
    if not _check_sklearn():
        return {}
    from sklearn.svm import SVC

    return _sklearn_per_number(
        draws_2d, max_num, lambda: SVC(kernel="rbf", probability=True, C=1.0, random_state=0)
    )


def score_ml_lda(draws_2d, max_num):
    if not _check_sklearn():
        return {}
    from sklearn.discriminant_analysis import LinearDiscriminantAnalysis

    return _sklearn_per_number(draws_2d, max_num, lambda: LinearDiscriminantAnalysis())


def score_ml_ridge(draws_2d, max_num):
    if not _check_sklearn():
        return {}
    from sklearn.linear_model import RidgeClassifier

    return _sklearn_per_number(draws_2d, max_num, lambda: RidgeClassifier(alpha=1.0))


def _has_catboost():
    try:
        import catboost  # noqa: F401

        return True
    except Exception:
        return False


def score_ml_catboost(draws_2d, max_num):
    if not _has_catboost():
        return {}
    from catboost import CatBoostClassifier

    return _sklearn_per_number(
        draws_2d,
        max_num,
        lambda: CatBoostClassifier(
            iterations=50, depth=4, verbose=False, allow_writing_files=False
        ),
    )


def score_ml_complement_nb(draws_2d, max_num):
    if not _check_sklearn():
        return {}
    from sklearn.naive_bayes import ComplementNB

    return _sklearn_per_number(draws_2d, max_num, lambda: ComplementNB())


def score_ml_passive_aggressive(draws_2d, max_num):
    if not _check_sklearn():
        return {}
    from sklearn.linear_model import PassiveAggressiveClassifier

    return _sklearn_per_number(
        draws_2d,
        max_num,
        lambda: PassiveAggressiveClassifier(max_iter=400, random_state=42),
    )


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


ML_METHODS: dict[str, tuple[Callable, str, bool, str]] = {
    "ml_complement_nb": (
        score_ml_complement_nb,
        "ml-bayes",
        True,
        "Complement Naive Bayes",
    ),
    "ml_passive_aggressive": (
        score_ml_passive_aggressive,
        "ml-linear",
        True,
        "Passive-Aggressive",
    ),
    "ml_ridge_cv": (score_ml_ridge_cv, "ml-linear", True, "Ridge Classifier CV"),
    "ml_nearest_centroid": (
        score_ml_nearest_centroid,
        "ml-knn",
        True,
        "Nearest Centroid",
    ),
    "ml_logistic": (
        score_ml_logistic,
        "ml-linear",
        True,
        "Logistic Regression (sklearn)",
    ),
    "ml_ridge": (score_ml_ridge, "ml-linear", True, "Ridge Classifier"),
    "ml_lda": (score_ml_lda, "ml-linear", True, "Linear Discriminant Analysis"),
    "ml_decision_tree": (score_ml_decision_tree, "ml-tree", True, "Decision Tree"),
    "ml_knn_5": (score_ml_knn_5, "ml-knn", True, "K-NN k=5"),
    "ml_svm_rbf": (score_ml_svm_rbf, "ml-kernel", True, "SVM RBF kernel"),
    "ml_catboost": (score_ml_catboost, "ml-boost", True, "CatBoost"),
}
