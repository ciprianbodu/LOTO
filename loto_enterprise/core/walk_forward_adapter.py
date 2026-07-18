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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from loto_enterprise.core.backtesting import scored_variant_numbers
from loto_enterprise.core.py314_io import pickle_load_path, pickle_store_path_atomic

logger = logging.getLogger(__name__)

CACHE_DIR = Path("bench_results")
CACHE_VERSION = "v9"  # v9: pool top-N pur + OMNIUS top-N (optimizare hits 3+)
# v8: 649_katz12_gap88 scorer 6/49 k16


@dataclass
class WalkForwardResult:
    """Per (draw, variant) entry — drop-in pentru UI care aşteaptă lista flat."""
    draw_index: int
    draw_date: str | None
    variant: list[int]
    hits: int
    hits_union: int  # cât din pool a nimerit per extragere
    target_draw_date: str | None = None  # alias pt RetroactivePrediction
    omnius_hits: int = 0  # cât a nimerit biletul OMNIUS la această extragere (per-draw)
    omnius_ticket: list[int] = field(default_factory=list)  # biletul OMNIUS retroactiv

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


def _cache_path(game_type: str, csv_hash: str, pool_size: int, depth: int, dec_sig: str,
                auto_invert: bool = False) -> Path:
    safe = game_type.replace("/", "_")
    CACHE_DIR.mkdir(exist_ok=True, parents=True)
    inv = "_inv" if auto_invert else ""
    return CACHE_DIR / f"walk_forward_{CACHE_VERSION}_{safe}_{csv_hash}_pool{pool_size}_d{depth}_{dec_sig}{inv}.pkl"


def expand_predictions_to_flat(
    preds: list[Any], game_type: str
) -> list[WalkForwardResult]:
    """RetroactivePrediction[N draws] → WalkForwardResult[N draws × M variants].

    Fiecare RetroactivePrediction conține lista de variante prezise pentru o
    extragere. UI consumă lista flat (variantă × extragere). Expand explicit.
    """
    flat: list[WalkForwardResult] = []
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
    auto_invert: bool = False,
) -> tuple[list[WalkForwardResult], dict]:
    """Run walk-forward backtest (or load from cache).

    Returns:
        (flat_results, meta_dict)
        meta_dict include: from_cache (bool), n_predictions, n_test_draws, csv_hash
    """
    csv_hash = _csv_hash(df_source, game_type)
    dec_sig = _decision_sig(game_type, pool_size)
    cache_file = _cache_path(game_type, csv_hash, pool_size, int(backtest_depth_percent), dec_sig,
                            auto_invert=auto_invert)
    meta = {
        "csv_hash": csv_hash,
        "decision_sig": dec_sig,
        "cache_file": str(cache_file),
        "from_cache": False,
        "game_type": game_type,
        "pool_size": pool_size,
        "backtest_depth_percent": backtest_depth_percent,
        "auto_invert": bool(auto_invert),
    }

    cached = None
    if use_cache and not force_refresh and cache_file.exists():
        try:
            cached = pickle_load_path(cache_file)
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
        f"{' (Pool 2 / inversare)' if auto_invert else ''}"
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
        smart_reduction=False,
        progress_cb=progress_cb,      # frac 0..1 per simulare → bară de progres în UI
        should_cancel=should_cancel,  # oprire timpurie (anulare/buget timp) → validare parțială
        auto_invert=auto_invert,
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
        pickle_store_path_atomic(cache_file, {"flat": flat, **meta})
        logger.info(f"[WALK-FWD] Cache saved → {cache_file}")
    except Exception as exc:
        logger.warning(f"[WALK-FWD] Cache save failed: {exc}")

    return flat, meta


def build_retrospective_pool_hits_flat(
    flat_reference: list[WalkForwardResult],
    df_source: pd.DataFrame,
    game_type: str,
    pool_numbers: list[int],
    variants: list,
    omnius_ticket: list[int],
) -> tuple[list[WalkForwardResult], dict]:
    """Istoric hits Pool 2 / OMNIUS 2 fără walk-forward onest.

    Folosește ACELEAȘI extrageri ca WF Pool 1, dar pool-ul + wheel-ul CURENT
    (generat azi). Rapid (secunde): nu regenerează pipeline-ul la fiecare pas.
    Informativ pentru plasa de siguranță — NU înlocuiește validarea WF Pool 1.
    """
    from loto_enterprise.core.backtesting import LotoBacktester

    if not flat_reference or not pool_numbers:
        return [], {"retrospective": True, "n_test_draws": 0, "partial": False}

    bt = LotoBacktester(df_source, game_type=game_type)
    draw_n = int({"6/49": 6, "5/40": 5, "joker": 5}.get(game_type, 6))
    pool_set = {int(x) for x in pool_numbers}
    omni_nums = [int(x) for x in (omnius_ticket or [])[:draw_n]]
    omni_set = set(omni_nums)
    variants = variants or []

    draw_indices = sorted({int(getattr(p, "draw_index", -1)) for p in flat_reference} - {-1})
    flat_out: list[WalkForwardResult] = []
    n_draws = 0

    for di in draw_indices:
        if di < 0 or di >= len(bt.draws):
            continue
        raw = bt.draws[di]
        actual = set(int(x) for x in raw[:draw_n])
        dd = bt.dates[di] if di < len(bt.dates) else None
        hits_union = len(pool_set & actual)
        omnius_hits = len(omni_set & actual)
        n_draws += 1

        if variants:
            for v in variants:
                vset = set(scored_variant_numbers(v, game_type))
                flat_out.append(WalkForwardResult(
                    draw_index=di,
                    draw_date=str(dd) if dd else None,
                    variant=list(v),
                    hits=len(vset & actual),
                    hits_union=hits_union,
                    target_draw_date=str(dd) if dd else None,
                    omnius_hits=omnius_hits,
                    omnius_ticket=list(omni_nums),
                ))
        else:
            flat_out.append(WalkForwardResult(
                draw_index=di,
                draw_date=str(dd) if dd else None,
                variant=[],
                hits=hits_union,
                hits_union=hits_union,
                target_draw_date=str(dd) if dd else None,
                omnius_hits=omnius_hits,
                omnius_ticket=list(omni_nums),
            ))

    meta = {
        "retrospective": True,
        "n_test_draws": n_draws,
        "n_expected": n_draws,
        "partial": False,
        "from_cache": False,
        "auto_invert": True,
    }
    logger.info(
        "[WALK-FWD] Retrospectiv Pool 2 %s: %d extrageri, %d intrări flat (fără WF)",
        game_type, n_draws, len(flat_out),
    )
    return flat_out, meta


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
