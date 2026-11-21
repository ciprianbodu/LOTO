"""Active scoring catalog shared by generation, Re-Bench and validation.

Scorers receive the draw matrix in chronological order. Methods marked
`_uses_history_cutoffs` also receive the start of each draw date.
"""

from __future__ import annotations

import logging
import time
from typing import Callable, Sequence

import numpy as np

logger = logging.getLogger(__name__)

CATALOG_VERSION = "compact-v1"


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


from .methods_common import normalize as _normalize  # noqa: E402


# ---------------------------------------------------------------------------
# Baselines
# ---------------------------------------------------------------------------


def score_random(draws_2d: np.ndarray, max_num: int) -> dict[int, float]:
    """Baseline structural: scoruri uniforme, INDEPENDENTE de istoric ca semnal.

    Seed derivat determinist din istoric + univers: aceeasi fereastra da mereu
    aceeasi realizare, deci randul `random` din folds.csv este reproductibil
    intre rulari si nu depinde de ce realizare a fost cache-uita prima.
    Fiecare bloc primeste alt istoric, deci alta realizare.
    """
    import hashlib as _hashlib

    _body = (
        np.ascontiguousarray(np.asarray(draws_2d)).tobytes()
        if draws_2d is not None
        else b""
    )
    _digest = _hashlib.blake2b(
        _body + int(max_num).to_bytes(4, "little"), digest_size=8
    ).digest()
    rng = np.random.default_rng(int.from_bytes(_digest, "little"))
    return _normalize({n: float(rng.random()) for n in range(1, max_num + 1)}, max_num)


def score_frequency(draws_2d: np.ndarray, max_num: int) -> dict[int, float]:
    """Recency-weighted frequency (exponential decay)."""
    n = draws_2d.shape[0]
    if n == 0:
        return _normalize({i: 1.0 for i in range(1, max_num + 1)}, max_num)
    weights = np.exp(np.linspace(-2.0, 0.0, n)).astype(np.float32)
    values = np.asarray(draws_2d).astype(np.int64).ravel()
    repeated_weights = np.repeat(weights.astype(np.float64), draws_2d.shape[1])
    valid = (values >= 1) & (values <= max_num)
    # bincount accumulates in row order, preserving the float32 weights and
    # float64 sums of the reference loop while avoiding a Python loop per ball.
    scores = np.bincount(
        values[valid], weights=repeated_weights[valid], minlength=max_num + 1
    )
    return _normalize({i: float(scores[i]) for i in range(1, max_num + 1)}, max_num)


# ---------------------------------------------------------------------------
# Registry (group → method name → (callable, family, requires_train, notes))
# ---------------------------------------------------------------------------

# Method tuple: (callable, family, requires_train, notes)
BASELINES: dict[str, tuple[Callable, str, bool, str]] = {
    "random": (score_random, "baseline", False, "Pure-random scores; sanity floor"),
    "frequency": (
        score_frequency,
        "baseline",
        False,
        "Exp-decay recency-weighted frequency",
    ),
}


METHODS = BASELINES.copy()
METHOD_LOAD_ERRORS: dict[str, str] = {}


def _load_extra_methods() -> None:
    """Keep the two references available if the scoring module fails to load."""
    METHODS.clear()
    METHODS.update(BASELINES)
    METHOD_LOAD_ERRORS.clear()
    try:
        from .methods_compact import COMPACT_METHODS
    except Exception as exc:
        METHOD_LOAD_ERRORS["methods_compact"] = f"{type(exc).__name__}: {exc}"
        logger.exception("Nu s-a putut încărca modulul de scorare methods_compact.")
        return
    for name, definition in COMPACT_METHODS.items():
        if name in METHODS:
            logger.error("Nume duplicat în catalogul de metode: %s", name)
        else:
            METHODS[name] = definition


_load_extra_methods()

# Preserved API for saved configurations. Retired scorers have no live aliases.
METHOD_ALIASES: dict[str, str] = {}


def resolve_method_name(name: str) -> str:
    return METHOD_ALIASES.get(name, name)


def list_methods() -> list[str]:
    return list(METHODS)


def require_complete_catalog() -> None:
    """A normal benchmark or validation needs both references and all three scorers."""
    if (
        not METHOD_LOAD_ERRORS
        and BASELINES.keys() <= METHODS.keys()
        and sum(name.startswith("compact_") for name in METHODS) == 3
    ):
        return
    details = ", ".join(f"{module}: {error}" for module, error in METHOD_LOAD_ERRORS.items())
    raise RuntimeError(f"Catalogul {CATALOG_VERSION} este incomplet ({details or 'înregistrare incompletă'}).")


def method_meta(name: str) -> dict:
    name = resolve_method_name(name)
    if name not in METHODS:
        return {
            "name": name,
            "family": "unknown",
            "requires_train": False,
            "notes": "eliminat din METHODS / necunoscut",
            "available": False,
            "unavailable_reason": "not_in_registry",
        }
    fn, family, requires_train, notes = METHODS[name]
    available = not getattr(fn, "_unavailable_reason", None)
    meta = {
        "name": name,
        "family": family,
        "requires_train": requires_train,
        "notes": notes,
        "available": available,
    }
    if getattr(fn, "_uses_history_cutoffs", False):
        meta["uses_history_cutoffs"] = True
    reason = getattr(fn, "_unavailable_reason", None)
    if reason:
        meta["unavailable_reason"] = reason
    return meta


def call_method(
    name: str, draws_2d: np.ndarray, max_num: int, *,
    history_cutoffs: Sequence[int] | None = None,
) -> tuple[dict[int, float], float]:
    """Call a scorer, optionally with day boundaries aligned to its history.

    Existing scorers retain the two-argument contract. A context-aware method
    receives only boundaries for the supplied prefix, never target/future rows.
    """
    name = resolve_method_name(name)
    if name not in METHODS:
        raise KeyError(f"method {name!r} not in METHODS (eliminat / necunoscut)")
    fn, _family, _train, _notes = METHODS[name]
    return evaluate_scorer(fn, draws_2d, max_num, history_cutoffs)


def evaluate_scorer(fn, draws_2d, max_num, history_cutoffs):
    """Evaluate a resolved scorer with the date context its contract accepts."""
    t0 = time.perf_counter()
    if getattr(fn, "_uses_history_cutoffs", False):
        scores = fn(draws_2d, max_num, history_cutoffs=history_cutoffs)
    else:
        scores = fn(draws_2d, max_num)
    dt = time.perf_counter() - t0
    return scores, dt
