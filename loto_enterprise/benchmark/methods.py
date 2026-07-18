"""Unified scoring interface for every model in the benchmark.

Each scorer is a callable that accepts:
    * draws_2d : np.ndarray of shape (n_draws, draw_n) with the history of
                 drawn numbers (per row)
    * max_num  : int — universe size (e.g. 49 for 6/49)

…and returns ``dict[int, float]`` keyed by candidate number (1..max_num),
with scores normalized to [0, 1] (higher = more likely to be drawn next).

A METHODS dict at the bottom registers every available method. Extra CPU
methods (classical / ML / coverage / graph / omnius) are merged from extension
modules at import time.

NOTĂ: tot GPU-ul a fost eliminat din aplicație — nu mai există metode
neural/foundation/torch/TimesFM aici. Benchmark-ul rulează exclusiv metode CPU.
"""

from __future__ import annotations

import logging
import time
import warnings
from typing import Callable

import numpy as np

warnings.filterwarnings("ignore")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Utilities
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


# ---------------------------------------------------------------------------
# Baselines
# ---------------------------------------------------------------------------

def score_random(draws_2d: np.ndarray, max_num: int) -> dict[int, float]:
    rng = np.random.default_rng()
    return _normalize({n: float(rng.random()) for n in range(1, max_num + 1)}, max_num)


def score_frequency(draws_2d: np.ndarray, max_num: int) -> dict[int, float]:
    """Recency-weighted frequency (exponential decay)."""
    n = draws_2d.shape[0]
    if n == 0:
        return _normalize({i: 1.0 for i in range(1, max_num + 1)}, max_num)
    weights = np.exp(np.linspace(-2.0, 0.0, n)).astype(np.float32)
    scores = np.zeros(max_num + 1, dtype=np.float64)
    for w, row in zip(weights, draws_2d):
        for v in row:
            vi = int(v)
            if 1 <= vi <= max_num:
                scores[vi] += w
    return _normalize({i: float(scores[i]) for i in range(1, max_num + 1)}, max_num)


def score_recency(draws_2d: np.ndarray, max_num: int) -> dict[int, float]:
    """Gap-based: longer time since last appearance → higher score (overdue)."""
    n = draws_2d.shape[0]
    last_seen = np.full(max_num + 1, -1, dtype=np.int32)
    for i, row in enumerate(draws_2d):
        for v in row:
            vi = int(v)
            if 1 <= vi <= max_num:
                last_seen[vi] = i
    scores = {}
    for num in range(1, max_num + 1):
        if last_seen[num] < 0:
            scores[num] = float(n + 1)
        else:
            scores[num] = float(n - last_seen[num])
    return _normalize(scores, max_num)


# ---------------------------------------------------------------------------
# Registry (group → method name → (callable, family, requires_train, notes))
# ---------------------------------------------------------------------------

# Method tuple: (callable, family, requires_train, notes)
METHODS: dict[str, tuple[Callable, str, bool, str]] = {
    # Baselines
    "random":      (score_random,      "baseline",        False, "Pure-random scores; sanity floor"),
    "frequency":   (score_frequency,   "baseline",        False, "Exp-decay recency-weighted frequency"),
    "recency":     (score_recency,     "baseline",        False, "Gap-since-last-seen ('overdue' heuristic)"),
}


# ============================================================================
# EXTENSIONS — extra CPU methods loaded from methods_classical.py, methods_ml.py,
# methods_coverage.py, methods_omnius.py, methods_graph.py. Loaded lazily; if any
# module is missing or import fails, the loader logs and continues.
# (Modulele GPU — torch_extra / torch_advanced / geometry — au fost eliminate.)
# ============================================================================
def _load_extra_methods() -> None:
    """Merge METHODS dicts from CPU extension modules into the global METHODS."""
    global METHODS
    extensions = []
    try:
        from . import methods_classical
        extensions.append(("methods_classical", methods_classical.CLASSICAL_METHODS))
    except Exception as exc:
        logger.debug(f"[methods] methods_classical not loaded: {exc}")
    try:
        from . import methods_ml
        extensions.append(("methods_ml", methods_ml.ML_METHODS))
    except Exception as exc:
        logger.debug(f"[methods] methods_ml not loaded: {exc}")
    try:
        from . import methods_coverage
        extensions.append(("methods_coverage", methods_coverage.COVERAGE_METHODS))
    except Exception as exc:
        logger.debug(f"[methods] methods_coverage not loaded: {exc}")
    try:
        from . import methods_omnius
        extensions.append(("methods_omnius", methods_omnius.OMNIUS_METHODS))
    except Exception as exc:
        logger.debug(f"[methods] methods_omnius not loaded: {exc}")
    try:
        from . import methods_graph
        extensions.append(("methods_graph", methods_graph.GRAPH_METHODS))
    except Exception as exc:
        logger.debug(f"[methods] methods_graph not loaded: {exc}")
    try:
        from . import methods_search_649
        extensions.append(("methods_search_649", methods_search_649.SEARCH_649_NEW))
    except Exception as exc:
        logger.debug(f"[methods] methods_search_649 not loaded: {exc}")
    try:
        from . import methods_top649
        extensions.append(("methods_top649", methods_top649.TOP649_METHODS))
    except Exception as exc:
        logger.debug(f"[methods] methods_top649 not loaded: {exc}")

    added = 0
    for modname, extra_dict in extensions:
        for name, tup in extra_dict.items():
            if name not in METHODS:
                METHODS[name] = tup
                added += 1
    if added > 0:
        logger.info(f"[methods] Loaded {added} extra prediction methods from extensions ({len(extensions)} modules).")


# Alias-uri pentru nume vechi (înainte de eliminarea GPU: ml_*_cpu). Nu intră în bench
# (list_methods le exclude) — doar rezolvă best_methods.json / folds.csv vechi până la re-bench.
METHOD_ALIASES: dict[str, str] = {
    "ml_xgb_cpu": "ml_xgb",
    "ml_lgbm_cpu": "ml_lgbm",
    "ml_catboost_cpu": "ml_catboost",
}


def resolve_method_name(name: str) -> str:
    """Mapează nume legacy (ex. ml_catboost_cpu) la numele curent din registry."""
    return METHOD_ALIASES.get(name, name)


# Load extensions at module import time.
try:
    _load_extra_methods()
except Exception as _ext_exc:
    logger.warning(f"[methods] Extra methods load failed: {_ext_exc}")


def list_methods() -> list[str]:
    return [n for n in METHODS if n not in METHOD_ALIASES]


def method_meta(name: str) -> dict:
    name = resolve_method_name(name)
    fn, family, requires_train, notes = METHODS[name]
    available = not getattr(fn, "_unavailable_reason", None)
    meta = {
        "name": name,
        "family": family,
        "requires_train": requires_train,
        "notes": notes,
        "available": available,
    }
    reason = getattr(fn, "_unavailable_reason", None)
    if reason:
        meta["unavailable_reason"] = reason
    return meta


def call_method(name: str, draws_2d: np.ndarray, max_num: int) -> tuple[dict[int, float], float]:
    """Call a registered method; returns (scores_dict, wall_time_sec)."""
    fn, _family, _train, _notes = METHODS[name]
    t0 = time.perf_counter()
    scores = fn(draws_2d, max_num)
    dt = time.perf_counter() - t0
    return scores, dt
