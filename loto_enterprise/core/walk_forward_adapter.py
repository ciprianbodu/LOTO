"""Walk-forward backtest adapter — onest (FĂRĂ data leakage).

Înlocuieşte evaluate_variants (care folosea pool-ul azi vs istoric întreg) cu
run_retroactive_backtest care, pentru fiecare extragere t, regenerează pool-ul
folosind DOAR datele < t. Asta elimină recency bias şi reflectă cu acuratețe
puterea predictivă reală.

Cache:
    - Pe disc: bench_results/walk_forward_<game>_<csv_hash>_<pool>_<depth>.pkl
    - Reutilizat la următorul Auto-Pilot dacă (csv_hash, pool_size) match
    - Curățat manual cu clear_walk_forward_cache()
"""

from __future__ import annotations

import hashlib
import json
import logging
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

CACHE_DIR = Path("bench_results")


@dataclass
class WalkForwardResult:
    """Per (draw, variant) entry — drop-in pentru UI care aşteaptă lista flat."""
    draw_index: int
    draw_date: Optional[str]
    variant: List[int]
    hits: int
    hits_union: int  # cât din pool a nimerit per extragere
    target_draw_date: Optional[str] = None  # alias pt RetroactivePrediction

    def __post_init__(self):
        if self.target_draw_date is None:
            self.target_draw_date = self.draw_date


def _csv_hash(df: pd.DataFrame, game_type: str) -> str:
    """Stable MD5 hash al ultimelor 500 rânduri × coloanele de numere."""
    cols_map = {
        "6/49":   ["n1", "n2", "n3", "n4", "n5", "n6"],
        "5/40":   ["n1", "n2", "n3", "n4", "n5"],
        "joker":  ["n1", "n2", "n3", "n4", "n5", "joker"],
    }
    cols = [c for c in cols_map.get(game_type, []) if c in df.columns]
    if not cols:
        return hashlib.md5(str(len(df)).encode()).hexdigest()[:12]
    sub = df[cols].tail(500)
    h = hashlib.md5(sub.to_csv(index=False, header=False).encode()).hexdigest()
    return h[:12]


def _cache_path(game_type: str, csv_hash: str, pool_size: int, depth: int) -> Path:
    safe = game_type.replace("/", "_")
    CACHE_DIR.mkdir(exist_ok=True, parents=True)
    return CACHE_DIR / f"walk_forward_{safe}_{csv_hash}_pool{pool_size}_d{depth}.pkl"


def expand_predictions_to_flat(
    preds: List[Any], game_type: str
) -> List[WalkForwardResult]:
    """RetroactivePrediction[N draws] → WalkForwardResult[N draws × M variants].

    Fiecare RetroactivePrediction conține lista de variante prezise pentru o
    extragere. UI consumă lista flat (variantă × extragere). Expand explicit.
    """
    flat: List[WalkForwardResult] = []
    for p in preds:
        actual = set(p.actual_numbers)
        for variant in p.variants:
            vset = set(variant)
            hits = len(vset & actual)
            flat.append(WalkForwardResult(
                draw_index=p.draw_index,
                draw_date=p.target_draw_date,
                variant=list(variant),
                hits=hits,
                hits_union=p.hits_union,
                target_draw_date=p.target_draw_date,
            ))
    return flat


def run_honest_walk_forward(
    df_source: pd.DataFrame,
    game_type: str,
    pool_size: int,
    backtest_depth_percent: float = 5.0,
    lookback_percent: float = 100.0,
    use_cache: bool = True,
    force_refresh: bool = False,
    progress_cb=None,
) -> Tuple[List[WalkForwardResult], dict]:
    """Run walk-forward backtest (or load from cache).

    Returns:
        (flat_results, meta_dict)
        meta_dict include: from_cache (bool), n_predictions, n_test_draws, csv_hash
    """
    csv_hash = _csv_hash(df_source, game_type)
    cache_file = _cache_path(game_type, csv_hash, pool_size, int(backtest_depth_percent))
    meta = {
        "csv_hash": csv_hash,
        "cache_file": str(cache_file),
        "from_cache": False,
        "game_type": game_type,
        "pool_size": pool_size,
        "backtest_depth_percent": backtest_depth_percent,
    }

    if use_cache and not force_refresh and cache_file.exists():
        try:
            with open(cache_file, "rb") as f:
                cached = pickle.load(f)
            meta["from_cache"] = True
            meta["n_predictions"] = cached["n_predictions"]
            meta["n_test_draws"] = cached["n_test_draws"]
            logger.info(
                f"[WALK-FWD] Cache hit pentru {game_type} pool={pool_size} "
                f"hash={csv_hash} ({len(cached['flat'])} entries)"
            )
            return cached["flat"], meta
        except Exception as exc:
            logger.warning(f"[WALK-FWD] Cache load failed: {exc} — re-run")

    # Cache miss → rulează walk-forward genuin
    from loto_enterprise.core.backtesting import LotoBacktester
    logger.info(
        f"[WALK-FWD] Cache miss — rulez walk-forward genuin pentru {game_type} "
        f"pool={pool_size} depth={backtest_depth_percent}%"
    )
    bt = LotoBacktester(df_source, game_type=game_type)
    predictions = bt.run_retroactive_backtest(
        pool_size=pool_size,
        guarantee=max(4, pool_size // 3),
        lookback_percent=lookback_percent,
        backtest_depth_percent=backtest_depth_percent,
        filter_consecutives=False,
        max_variants=0,
        simulation_step=1,
        use_feedback=False,           # decuplat pentru a măsura PUR ce face engine-ul
        enable_hard_inversion=False,  # idem
        smart_reduction=True,
        progress_cb=progress_cb,      # frac 0..1 per simulare → bară de progres în UI
    )

    flat = expand_predictions_to_flat(predictions, game_type)
    meta["n_predictions"] = len(predictions)
    meta["n_test_draws"] = len(set(p.draw_index for p in predictions))

    # Save cache
    try:
        with open(cache_file, "wb") as f:
            pickle.dump({"flat": flat, **meta}, f)
        logger.info(f"[WALK-FWD] Cache saved → {cache_file}")
    except Exception as exc:
        logger.warning(f"[WALK-FWD] Cache save failed: {exc}")

    return flat, meta


def clear_walk_forward_cache() -> int:
    """Şterge toate cache-urile walk-forward; returnează numărul de fişiere şterse."""
    if not CACHE_DIR.exists():
        return 0
    deleted = 0
    for f in CACHE_DIR.glob("walk_forward_*.pkl"):
        try:
            f.unlink()
            deleted += 1
        except Exception:
            pass
    return deleted
