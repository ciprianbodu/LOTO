"""Benchmark fold-level disk cache + adaptive coarse-to-fine sweep helpers.

PROBLEMA OPTIMIZATA:
    Bench-ul matricial e (metode × jocuri × percentile × pool_sizes) folds. Cu
    ~150 metode in registry, matricea e ~36k+ folds, costand 3-8h pe GPU.

SOLUTII:
    1. **Disk cache per fold** — keyed pe (csv_hash, method, percentile, game).
       Daca CSV-ul nu s-a schimbat, fold-ul deja calculat se reutilizeaza
       instant. La un Full Re-Bench nemodificat = 0 secunde (toate hit cache).
       La un drift mic (CSV cu 50 randuri noi), doar fold-urile metode care
       sufera de drift sunt re-calculate.

    2. **Adaptive coarse-to-fine sweep** — primul pass folosese DOAR 3
       percentile (30/60/100), elimina top-N performers, apoi face full sweep
       (toate 10 percentile × toate pool sizes) doar pe survivors.
       Reduce efectiv folds cu 4-6x.

USAGE:
    from loto_enterprise.benchmark.bench_cache import (
        compute_csv_hash, get_cached_fold, store_cached_fold,
        adaptive_method_filter,
    )

    # In runner.run_benchmark:
    csv_hash = compute_csv_hash(draws)
    cached = get_cached_fold(csv_hash, method, pct, game.key)
    if cached is not None:
        return cached
    # ... compute fold ...
    store_cached_fold(csv_hash, method, pct, game.key, result)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import MISSING, fields, is_dataclass
from pathlib import Path
from typing import Any

import numpy as np

from loto_enterprise.core.py314_io import pickle_load_path, pickle_store_path

logger = logging.getLogger(__name__)

def _resolve_cache_dir() -> Path:
    """Cache-ul stă ÎN AFARA OneDrive (ca .venv) — altfel zeci de mii de fișiere mici
    se sincronizează degeaba și pot fi corupte la sync parțial. Implicit pe stația
    ALF-LUPTATORI: D:\\_BUILD\\_LOTO\\.bench_cache. Override: LOTO_BENCH_CACHE_DIR.
    Fallback: local .bench_cache (container/CI/altă stație fără D:\\_BUILD)."""
    env = os.environ.get("LOTO_BENCH_CACHE_DIR")
    if env:
        return Path(env)
    if os.name == "nt":
        base = Path(r"D:\_BUILD\_LOTO")
        if base.exists():
            return base / ".bench_cache"
    return Path(".bench_cache")


CACHE_DIR = _resolve_cache_dir()
CACHE_VERSION = "v12"
# Changelog (cea mai nouă prima; bump = invalidare TOTALĂ, re-bench complet):
# v12: parity_balance + 649_parity_recent: tie-break pe frecvență (nu mai degenera
#      la „cele mai MARI pare/impare" via rank_by_score pe 2 nivele).
# v11: score_sum_affinity nu mai e gaussiană pe |k − medie/n| (Joker top-11 era
#      MEREU 18–28 consecutiv). Scorul e masa de extrageri cu sumă tipică care
#      conțin k — output-ul metodei se schimbă, cache-ul v10 e stale.
# v10: tie-break unificat + ponderi engine + registry fără duplicatul 649_top_autocorr (alias → autocorr).
# v9:  prime_bias + frecvență (nu mai degenera la „cele mai mici compuse”).
# v8:  nefolosită (sărită la bump-ul v7→v9).
# v7:  top649 649_katz12_gap88 (12% Katz + 88% gap_poisson).
# v6:  nefolosită (sărită).
# v5:  nefolosită (sărită).
# v4:  metodele graf folosesc graf de ASOCIERE (lift centrat) în loc de co-apariție brută.
# v3:  FoldResult are rate_3plus / rates_3plus_per_pool (target 3+/4+ configurabil).
# v2:  versiunea inițială.
INDEX_FILE = CACHE_DIR / "index.json"


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------

def compute_csv_hash(draws_2d: np.ndarray) -> str:
    """Hash al continutului array-ului — stabil intre rulari."""
    body = draws_2d.tobytes()
    return hashlib.md5(body).hexdigest()[:16]


def _fold_key(csv_hash: str, method: str, percentile: int, game_key: str, is_random: bool) -> str:
    raw = f"{CACHE_VERSION}::{csv_hash}::{game_key}::{method}::{percentile}::{is_random}"
    return hashlib.md5(raw.encode()).hexdigest()[:20]


# ---------------------------------------------------------------------------
# Disk cache I/O
# ---------------------------------------------------------------------------

def _ensure_cache_dir():
    CACHE_DIR.mkdir(exist_ok=True, parents=True)


def get_cached_fold(csv_hash: str, method: str, percentile: int, game_key: str,
                    is_random: bool = False) -> Any | None:
    """Return cached FoldResult or None."""
    _ensure_cache_dir()
    key = _fold_key(csv_hash, method, percentile, game_key, is_random)
    f = CACHE_DIR / f"{key}.pkl"
    if not f.exists():
        return None
    try:
        obj = pickle_load_path(f)
        # Backfill câmpuri noi adăugate în FoldResult DUPĂ ce s-a scris cache-ul
        # (ex. `family`) — altfel asdict()/acces atribut crapă pe obiecte vechi.
        if is_dataclass(obj):
            for fld in fields(obj):
                if not hasattr(obj, fld.name):
                    if fld.default is not MISSING:
                        setattr(obj, fld.name, fld.default)
                    elif fld.default_factory is not MISSING:  # type: ignore[misc]
                        setattr(obj, fld.name, fld.default_factory())  # type: ignore[misc]
                    else:
                        setattr(obj, fld.name, None)
        return obj
    except Exception as exc:
        logger.debug(f"[bench_cache] failed to load {f.name}: {exc}")
        try:
            f.unlink()  # corrupted; remove
        except Exception:
            pass
        return None


def store_cached_fold(csv_hash: str, method: str, percentile: int, game_key: str,
                       is_random: bool, result: Any) -> None:
    _ensure_cache_dir()
    key = _fold_key(csv_hash, method, percentile, game_key, is_random)
    f = CACHE_DIR / f"{key}.pkl"
    try:
        pickle_store_path(f, result)
    except Exception as exc:
        logger.warning(f"[bench_cache] failed to write {f.name}: {exc}")


def cache_stats() -> dict[str, int]:
    """Return number of cached folds + total disk size."""
    _ensure_cache_dir()
    files = list(CACHE_DIR.glob("*.pkl"))
    total_bytes = sum(f.stat().st_size for f in files)
    return {
        "n_folds_cached": len(files),
        "size_mb": round(total_bytes / 1024 / 1024, 2),
    }


def clear_cache(older_than_days: int | None = None) -> int:
    """Delete cached folds. Returns number deleted.

    Daca older_than_days e setat, doar folds mai vechi sunt sterse.
    """
    _ensure_cache_dir()
    deleted = 0
    cutoff = time.time() - (older_than_days * 86400) if older_than_days else None
    for f in CACHE_DIR.glob("*.pkl"):
        try:
            if cutoff is not None and f.stat().st_mtime > cutoff:
                continue
            f.unlink()
            deleted += 1
        except Exception:
            pass
    return deleted


# ---------------------------------------------------------------------------
# Adaptive coarse-to-fine sweep
# ---------------------------------------------------------------------------

def coarse_percentiles() -> list[int]:
    """3 percentile pentru pass-ul coarse — repere robuste."""
    return [30, 60, 100]


def fine_percentiles() -> list[int]:
    """10 percentile pentru pass-ul fine pe survivors."""
    return [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]


def select_survivors(coarse_results: list[dict], top_k_pct: float = 0.7,
                     min_keep: int = 10) -> list[str]:
    """
    Din rezultatele coarse, pastreaza top-K% metode (default 70%).

    coarse_results: lista de dict-uri cu cel putin keys: 'method', 'score'
    score = scor cumulat al metodei pe cele 3 percentile coarse (poate fi
    avg_hits, hits_4plus, sau orice metric agregat).

    Returnam lista de nume metode care supravietuiesc pentru fine sweep.
    """
    # Agregam scoruri per metoda
    method_scores: dict[str, float] = {}
    for r in coarse_results:
        method = r.get("method")
        if not method:
            continue
        method_scores[method] = method_scores.get(method, 0.0) + float(r.get("score", 0.0))
    if not method_scores:
        return []
    sorted_methods = sorted(method_scores.items(), key=lambda x: x[1], reverse=True)
    n_keep = max(min_keep, int(len(sorted_methods) * top_k_pct))
    survivors = [name for name, _ in sorted_methods[:n_keep]]
    return survivors


def estimate_time_savings(n_methods: int, n_percentiles_full: int = 10,
                          n_coarse: int = 3, survival_rate: float = 0.7) -> dict:
    """Estimare teoretica a economisirii de timp prin adaptive sweep."""
    full_folds = n_methods * n_percentiles_full
    adaptive_folds = (n_methods * n_coarse) + (int(n_methods * survival_rate) * n_percentiles_full)
    savings = 1.0 - (adaptive_folds / full_folds)
    return {
        "full_folds": full_folds,
        "adaptive_folds": adaptive_folds,
        "savings_pct": round(savings * 100, 1),
    }


# ---------------------------------------------------------------------------
# Pool size locality: skip pool sizes where winner is stable
# ---------------------------------------------------------------------------

def detect_stable_pool_neighborhoods(winners_per_pool: dict[str, str],
                                      min_run: int = 3) -> list[str]:
    """
    Detecteaza intervale de pool sizes unde castigatorul e acelasi.

    De exemplu, daca winners_per_pool = {k10: timesfm, k11: timesfm, k12: timesfm,
    k13: chronos, k14: chronos, k15: chronos, k16: patchtst, k17: patchtst},
    cu min_run=3, returnam ['k11', 'k14'] — mijlocul fiecarui run stabil.
    Aceste pool sizes pot fi sarite intr-un re-bench rapid.
    """
    if not winners_per_pool:
        return []
    keys = sorted(winners_per_pool.keys(), key=lambda x: int(x.replace("k", "")))
    runs = []
    cur_winner = None
    cur_start = None
    cur_len = 0
    for k in keys:
        w = winners_per_pool[k]
        if w == cur_winner:
            cur_len += 1
        else:
            if cur_len >= min_run:
                runs.append((cur_start, cur_len, cur_winner))
            cur_winner = w
            cur_start = k
            cur_len = 1
    if cur_len >= min_run:
        runs.append((cur_start, cur_len, cur_winner))
    # Returnam middle-ul fiecarui run (poate fi sarit la re-bench rapid)
    skippable = []
    for start_k, length, winner in runs:
        start_n = int(start_k.replace("k", ""))
        middle = start_n + length // 2
        if middle != start_n + length - 1:  # nu sarim end-ul
            skippable.append(f"k{middle}")
    return skippable


# ---------------------------------------------------------------------------
# Cache-aware wrapper for evaluate_fold
# ---------------------------------------------------------------------------

def cached_evaluate_fold(evaluate_fn, draws_for_hash: np.ndarray,
                         game_key: str, method: str, percentile: int,
                         is_random: bool, *args, **kwargs) -> Any:
    """
    Wrapper care consulta cache-ul inainte de a evalua.

    evaluate_fn: functia originala _evaluate_fold (din runner.py)
    draws_for_hash: array-ul de draws complete pentru hash (NU subsetul training)
    """
    csv_hash = compute_csv_hash(draws_for_hash)
    cached = get_cached_fold(csv_hash, method, percentile, game_key, is_random)
    if cached is not None:
        return cached
    result = evaluate_fn(*args, **kwargs)
    store_cached_fold(csv_hash, method, percentile, game_key, is_random, result)
    return result


def print_cache_summary() -> None:
    """Print stats — apelat la inceputul si finalul bench-ului."""
    stats = cache_stats()
    logger.info(
        f"[bench_cache] {stats['n_folds_cached']} folds cached "
        f"({stats['size_mb']} MB on disk in {CACHE_DIR})"
    )
