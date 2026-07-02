"""Walk-forward backtest adapter — onest (FĂRĂ data leakage).

Înlocuieşte evaluate_variants (care folosea pool-ul azi vs istoric întreg) cu
run_retroactive_backtest care, pentru fiecare extragere t, regenerează pool-ul
folosind DOAR datele < t. Asta elimină recency bias şi reflectă cu acuratețe
puterea predictivă reală.

Cache:
    - Pe disc: bench_results/walk_forward_<ver>_<game>_<csv_hash>_pool<N>_d<depth>_<dec_sig>.pkl
    - Reutilizat la următorul Auto-Pilot dacă (csv_hash, pool_size, decizie bench) match.
      dec_sig = semnătura deciziei (scorer/sim_depth/blacklist) → un Re-Bench care
      schimbă câştigătorul invalidează automat cache-ul (altfel valida cu metoda veche).
    - Curățat manual cu clear_walk_forward_cache()
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import pickle
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional, Tuple

import numpy as np
import pandas as pd

from loto_enterprise.core.backtesting import scored_variant_numbers

logger = logging.getLogger(__name__)

CACHE_DIR = Path("bench_results")
CACHE_VERSION = "v5"  # v5: iterare recent→vechi la oprire parțială (buget) — cache-urile
# v4 parțiale acopereau felia VECHE a ferestrei (ex. 6/49 doar 2014) → orfanizate.
# v4: flat-ul include omnius_hits/omnius_ticket per extragere.


@dataclass
class WalkForwardResult:
    """Per (draw, variant) entry — drop-in pentru UI care aşteaptă lista flat."""
    draw_index: int
    draw_date: Optional[str]
    variant: List[int]
    hits: int
    hits_union: int  # cât din pool a nimerit per extragere
    target_draw_date: Optional[str] = None  # alias pt RetroactivePrediction
    omnius_hits: int = 0  # cât a nimerit biletul OMNIUS la această extragere (per-draw)
    omnius_ticket: List[int] = field(default_factory=list)  # biletul OMNIUS retroactiv

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


def _decision_sig(game_type: str, pool_size: int) -> str:
    """Semnătură scurtă a deciziei bench (scorer + sim_depth + blacklist) pentru
    (joc, pool).

    Walk-forward-ul rulează engine-ul, care alege metoda câştigătoare din
    best_methods.json (per pool). Dacă semnătura NU intră în cheia de cache,
    un Re-Bench care schimbă câştigătorul ar servi o validare VECHE. Includem
    semnătura ca un câştigător nou să forţeze re-validare, iar o decizie
    neschimbată să reutilizeze cache-ul.
    """
    try:
        from loto_enterprise.core.method_selector import recommend_optimal_config
        gk = {"6/49": "loto_6_49", "5/40": "loto_5_40",
              "joker": "joker_urna1"}.get(game_type, "loto_6_49")
        c = recommend_optimal_config(gk, int(pool_size))
        raw = f"{c.get('scorer', '?')}|{c.get('sim_depth_pct', 0)}|{bool(c.get('use_blacklist', False))}"
        return hashlib.md5(raw.encode()).hexdigest()[:8]
    except Exception as exc:
        logger.warning(f"[WALK-FWD] decision sig indisponibilă ({exc}) — folosesc 'nodec'")
        return "nodec"


def _cache_path(game_type: str, csv_hash: str, pool_size: int, depth: int, dec_sig: str) -> Path:
    safe = game_type.replace("/", "_")
    CACHE_DIR.mkdir(exist_ok=True, parents=True)
    return CACHE_DIR / f"walk_forward_{CACHE_VERSION}_{safe}_{csv_hash}_pool{pool_size}_d{depth}_{dec_sig}.pkl"


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
            vset = set(scored_variant_numbers(variant, game_type))
            hits = len(vset & actual)
            flat.append(WalkForwardResult(
                draw_index=p.draw_index,
                draw_date=p.target_draw_date,
                variant=list(variant),
                hits=hits,
                hits_union=p.hits_union,
                target_draw_date=p.target_draw_date,
                omnius_hits=int(getattr(p, "omnius_hits", 0)),
                omnius_ticket=list(getattr(p, "omnius_ticket", []) or []),
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
    should_cancel=None,
) -> Tuple[List[WalkForwardResult], dict]:
    """Run walk-forward backtest (or load from cache).

    Returns:
        (flat_results, meta_dict)
        meta_dict include: from_cache (bool), n_predictions, n_test_draws, csv_hash
    """
    csv_hash = _csv_hash(df_source, game_type)
    dec_sig = _decision_sig(game_type, pool_size)
    cache_file = _cache_path(game_type, csv_hash, pool_size, int(backtest_depth_percent), dec_sig)
    meta = {
        "csv_hash": csv_hash,
        "decision_sig": dec_sig,
        "cache_file": str(cache_file),
        "from_cache": False,
        "game_type": game_type,
        "pool_size": pool_size,
        "backtest_depth_percent": backtest_depth_percent,
    }

    cached = None
    if use_cache and not force_refresh and cache_file.exists():
        try:
            with open(cache_file, "rb") as f:
                cached = pickle.load(f)
            if not cached.get("partial", False):
                # COMPLET → servim direct (fast path neschimbat).
                meta["from_cache"] = True
                meta["n_predictions"] = cached["n_predictions"]
                meta["n_test_draws"] = cached["n_test_draws"]
                meta["n_expected"] = cached.get("n_expected", cached["n_test_draws"])
                meta["partial"] = False
                logger.info(
                    f"[WALK-FWD] Cache hit pentru {game_type} pool={pool_size} "
                    f"hash={csv_hash} dec={dec_sig} ({len(cached['flat'])} entries)"
                )
                return cached["flat"], meta
            # PARȚIAL → NU-l servim orbește: re-rulăm ca să EXTINDEM acoperirea
            # (altfel un parțial rămâne înghețat până se schimbă CSV-ul, iar mărirea
            # bugetului WF din UI n-ar avea niciun efect). Dacă rularea nouă iese
            # MAI SCURTĂ decât cache-ul (mașină ocupată / anulare rapidă), păstrăm
            # cache-ul — ambele acoperă coada RECENTĂ a aceleiași ferestre, deci
            # cel mai lung îl conține strict pe cel mai scurt.
            logger.info(
                f"[WALK-FWD] Cache PARȚIAL pentru {game_type} pool={pool_size} "
                f"({cached.get('n_test_draws')}/{cached.get('n_expected')}) → re-rulez "
                f"pentru a extinde acoperirea (bugetul curent poate fi mai mare)."
            )
        except Exception as exc:
            cached = None
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
        should_cancel=should_cancel,  # oprire timpurie (anulare/buget timp) → validare parțială
    )

    # Câte simulări „ar fi trebuit" (pentru a marca validarea ca PARȚIALĂ în UI).
    n_expected = max(1, int(len(df_source) * backtest_depth_percent / 100.0))
    flat = expand_predictions_to_flat(predictions, game_type)
    meta["n_predictions"] = len(predictions)
    meta["n_test_draws"] = len(set(p.draw_index for p in predictions))
    meta["n_expected"] = n_expected
    meta["partial"] = meta["n_test_draws"] < n_expected

    # Rularea nouă a acoperit MAI PUȚIN decât cache-ul parțial existent (mașină
    # ocupată / anulare rapidă) → păstrăm cache-ul (mai acoperitor, aceeași coadă
    # recentă) și NU-l suprascriem cu regresia.
    if cached is not None and int(cached.get("n_test_draws") or 0) > meta["n_test_draws"]:
        logger.info(
            f"[WALK-FWD] Rularea nouă ({meta['n_test_draws']} extrageri) < cache "
            f"({cached.get('n_test_draws')}) → păstrez cache-ul (fără suprascriere)."
        )
        meta["from_cache"] = True
        meta["n_predictions"] = cached["n_predictions"]
        meta["n_test_draws"] = cached["n_test_draws"]
        meta["n_expected"] = cached.get("n_expected", n_expected)
        meta["partial"] = cached.get("partial", True)
        return cached["flat"], meta

    # Save cache (rulare nouă ≥ cache → suprascriem; scriere atomică anti-corupere
    # la UI-restart în mijlocul pickle.dump — un cache trunchiat ar crăpa la load).
    try:
        tmp_file = cache_file.with_suffix(".pkl.tmp")
        with open(tmp_file, "wb") as f:
            pickle.dump({"flat": flat, **meta}, f)
        os.replace(tmp_file, cache_file)
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
