"""Registry-ul unic al metodelor de scoring (setul din 14.09.2026).

Contract (CLAUDE.md §5.2): ``fn(draws_2d, max_num) -> dict[int, float]``,
scoruri în [0, 1], determinism, fără efecte secundare.

Registry-ul conține cele două baseline-uri structurale (`random` — martorul
benchmark-ului, interzis în producție; `frequency` — fallback-ul determinist
de producție) și cele 50 de metode din modulele:
    methods_recency.py     16 metode: recență, goluri, serii de timp
    methods_relational.py  10 metode: tranziții, co-apariție, graf, vecinătate
    methods_learning.py     4 metode: modele comune ieftine + media rangurilor
    methods_wave2.py       20 metode (al doilea val, 14.09.2026): idei reluate din
                           vechea listă disabled + descompuneri, context propriu,
                           relații de ordinul 2, învățare ieftină
Cele 181 de metode anterioare (8 module: classical, coverage, graph,
math_extra, ml, revived, search_649, top649 — nume unice numărate în
registrele lor, la commit-ul dinaintea înlocuirii) au fost eliminate la
14.09.2026, împreună cu mecanismul de tombstone (`disabled_methods.json`),
la cererea utilizatorului.

`neighbor_adjacent` și `repeat_last_draw` rămân în registry, pentru ca bench-ul
să le măsoare ca martori, dar sunt în `EXCLUDED_FROM_PRODUCTION`: top-K-ul lor
e o clasă geometrică (vecinii ultimei extrageri, respectiv ultima extragere),
nu un ranking (audit 2026-09-15).
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
METHODS: dict[str, tuple[Callable, str, bool, str]] = {
    # Baselines (recency a fost blacklistată și scoasă din registry)
    "random": (score_random, "baseline", False, "Pure-random scores; sanity floor"),
    "frequency": (
        score_frequency,
        "baseline",
        False,
        "Exp-decay recency-weighted frequency",
    ),
}


# Module de extensie care NU s-au încărcat: nume modul → mesajul excepției.
# Structură publică, populată (și resetată) la fiecare `_load_extra_methods()`.
# Un modul lipsă înseamnă zeci de metode absente din registry, deci producția
# cade pe `frequency`; fără această structură cauza reală (ex. o dependență
# neinstalată) rămânea doar într-o linie de log, iar utilizatorul vedea numai
# „metodă necunoscută". `method_selector._sanitize_production_name` o citește
# ca să numească modulul vinovat în chiar mesajul de fallback.
METHOD_LOAD_ERRORS: dict[str, str] = {}


# ============================================================================
# EXTENSIONS — metodele din methods_recency / methods_relational / methods_learning.
# Coliziunile de nume între module sunt logate, nu ascunse.
# ============================================================================
def _load_extra_methods() -> None:
    """Merge METHODS dicts from CPU extension modules into the global METHODS.

    Un modul care nu se încarcă NU oprește aplicația (contractul e pornire cu
    fallback determinist), dar nici nu dispare tăcut: eroarea intră în
    `METHOD_LOAD_ERRORS` și se loghează la nivel ERROR — pierderea a zeci de
    metode nu e un warning.
    """
    global METHODS
    # Reset la fiecare apel: reapelarea (teste, reload) nu acumulează erori vechi.
    METHOD_LOAD_ERRORS.clear()
    extensions = []
    for modname, attr in (
        ("methods_recency", "RECENCY_METHODS"),
        ("methods_relational", "RELATIONAL_METHODS"),
        ("methods_learning", "LEARNING_METHODS"),
        ("methods_wave2", "WAVE2_METHODS"),
    ):
        try:
            module = __import__(f"{__package__}.{modname}", fromlist=[attr])
            extensions.append((modname, getattr(module, attr)))
        except Exception as exc:  # noqa: BLE001
            METHOD_LOAD_ERRORS[modname] = f"{type(exc).__name__}: {exc}"
            logger.error(
                "[methods] modulul de metode %s NU s-a incarcat (%s: %s) — "
                "metodele lui LIPSESC din registry, iar productia va cadea pe "
                "fallback-ul determinist pentru orice nume din best_methods.json "
                "definit acolo.",
                modname,
                type(exc).__name__,
                exc,
            )

    added = 0
    skipped_collision = 0
    _owner: dict[str, str] = {}  # nume -> primul modul care l-a inregistrat
    for modname, extra_dict in extensions:
        for name, tup in extra_dict.items():
            if name not in METHODS:
                METHODS[name] = tup
                _owner[name] = modname
                added += 1
            elif _owner.get(name) != modname:
                # Coliziune REALĂ între două module de extensie (sau cu un nume
                # din registry-ul de bază): a doua implementare NU intră în bench
                # și se loghează, ca să nu dispară tăcut.
                skipped_collision += 1
                logger.warning(
                    "[methods] nume duplicat '%s' — pastrez implementarea din %s, "
                    "ignor cea din %s (a doua NU intra in bench).",
                    name,
                    _owner.get(name, "registry de baza"),
                    modname,
                )
    if added > 0:
        logger.info(
            f"[methods] Loaded {added} extra prediction methods from extensions ({len(extensions)} modules)."
        )
    if skipped_collision:
        logger.warning(
            "[methods] skipped %d duplicate (non-tombstone) names at load",
            skipped_collision,
        )


# Alias-uri de nume legacy → nume curent. Gol de la 14.09.2026: toate țintele
# vechi au fost eliminate; `resolve_method_name` rămâne ca punct unic de
# rezolvare pentru folds.csv / best_methods.json mai vechi decât registry-ul.
METHOD_ALIASES: dict[str, str] = {}


def resolve_method_name(name: str) -> str:
    """Mapează nume legacy (ex. ml_catboost_cpu) la numele curent din registry."""
    return METHOD_ALIASES.get(name, name)


# Load extensions at module import time.
try:
    _load_extra_methods()
except Exception as _ext_exc:  # noqa: BLE001
    # Nu propagăm (aplicația trebuie să pornească), dar consemnăm vizibil:
    # aici pică TOATE extensiile, nu un singur modul.
    METHOD_LOAD_ERRORS["_load_extra_methods"] = (
        f"{type(_ext_exc).__name__}: {_ext_exc}"
    )
    logger.error("[methods] Extra methods load failed: %s", _ext_exc)


def list_methods() -> list[str]:
    return [n for n in METHODS if n not in METHOD_ALIASES]


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
    reason = getattr(fn, "_unavailable_reason", None)
    if reason:
        meta["unavailable_reason"] = reason
    return meta


def call_method(
    name: str, draws_2d: np.ndarray, max_num: int
) -> tuple[dict[int, float], float]:
    """Call a registered method; returns (scores_dict, wall_time_sec)."""
    name = resolve_method_name(name)
    if name not in METHODS:
        raise KeyError(f"method {name!r} not in METHODS (eliminat / necunoscut)")
    fn, _family, _train, _notes = METHODS[name]
    t0 = time.perf_counter()
    scores = fn(draws_2d, max_num)
    dt = time.perf_counter() - t0
    return scores, dt
