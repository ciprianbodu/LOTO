"""Walk-forward backtest adapter — onest (FĂRĂ data leakage).

Înlocuieşte evaluate_variants (care folosea pool-ul azi vs istoric întreg) cu
run_retroactive_backtest care, pentru fiecare extragere t, regenerează pool-ul
folosind DOAR datele < t. Asta elimină recency bias şi reflectă cu acuratețe
puterea predictivă reală.

Cache:
    - Pe disc: bench_results/walk_forward_<ver>_<game>_<csv_hash>_pool<N>_d<depth>_<dec_sig>.pkl
      (CACHE_DIR e o cale RELATIVĂ, deci cache-ul stă ÎN repo/OneDrive — spre
      deosebire de cache-ul de bench, care e mutat în afara OneDrive.)
    - Reutilizat la următorul Auto-Pilot dacă (csv_hash, pool_size, decizie bench) match.
      dec_sig = semnătura deciziei (scorer/sim_depth/blacklist/ensemble) → un Re-Bench
      care schimbă câştigătorul sau compoziţia ensemble-ului invalidează automat
      cache-ul (altfel valida cu metoda veche).
    - Un bump de CACHE_VERSION doar schimbă NUMELE fişierului: pickle-urile vechi
      rămân pe disc la nesfârşit. `purge_stale_wf_cache()` le inventariază (implicit
      dry-run) şi le poate şterge; `clear_walk_forward_cache()` şterge TOT, inclusiv
      versiunea curentă.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from loto_enterprise.core.backtesting import scored_variant_numbers
from loto_enterprise.core.py314_io import pickle_load_path, pickle_store_path_atomic

logger = logging.getLogger(__name__)

CACHE_DIR = Path("bench_results")
CACHE_VERSION = "v13"
# Changelog (cea mai nouă prima; bump = invalidare cache walk-forward):
# v13: (a) flat-ul NU mai conține omnius_hits/omnius_ticket (biletul OMNIUS scos din
#      WF/UI) — structură incompatibilă cu v12; (b) DECORELAREA membrilor de ensemble
#      (method_selector.MAX_MEMBER_CORR=0.95, pe scoruri) + filtrul de redundanță din
#      decizie pot schimba POOL-ul generat față de v12 — deci cache-urile v12 validează
#      un pool care nu se mai generează; (c) `_decision_sig` include acum ensemble-ul
#      (compoziție + ponderi), nu doar `scorer`.
#      Motivul (b) e cel important: (a) singur ar fi fost compatibil la unpickle
#      (dataclass-ul se dezserializează fără __init__), deci NU justifica singur bump-ul.
# v12: tie-break unificat + ponderi engine + registry fără duplicatul 649_top_autocorr (alias → autocorr).
# v11: prime_bias tie-break frecvență + _decision_sig include BENCH_HIT_TARGET + tie-break pool.
# v10: nefolosită (sărită la bump-ul v9→v11).
# v9:  pool top-N pur + OMNIUS top-N (optimizare hits 3+).
# v8:  649_katz12_gap88 scorer 6/49 k16.
# v7:  nefolosită (sărită).
# v6:  sufix _inv.pkl pentru cache-ul Pool 2 (auto-invert).
# v5:  iterare recent→vechi la oprire parțială (buget) — cache-urile v4 parțiale acopereau felia veche.
# v4:  flat-ul include omnius_hits/omnius_ticket per extragere.
# v3:  cheia include semnătura deciziei bench (scorer/sim_depth/blacklist).
# v2:  versiunea inițială.


@dataclass
class WalkForwardResult:
    """Per (draw, variant) entry — drop-in pentru UI care aşteaptă lista flat."""
    draw_index: int
    draw_date: str | None
    variant: list[int]
    hits: int
    hits_union: int  # cât din pool a nimerit per extragere
    target_draw_date: str | None = None  # alias pt RetroactivePrediction

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
    """Semnătură scurtă a deciziei bench (scorer + sim_depth + blacklist + target) pentru
    (joc, pool).

    Walk-forward-ul rulează engine-ul, care alege metoda câştigătoare din
    best_methods.json (per pool). Dacă semnătura NU intră în cheia de cache,
    un Re-Bench care schimbă câştigătorul ar servi o validare VECHE. Includem
    semnătura ca un câştigător nou să forţeze re-validare, iar o decizie
    neschimbată să reutilizeze cache-ul.
    """
    try:
        from loto_enterprise.core.method_selector import recommend_optimal_config
        from loto_enterprise.benchmark.decision import BENCH_HIT_TARGET
        gk = {"6/49": "loto_6_49", "5/40": "loto_5_40",
              "joker": "joker_urna1"}.get(game_type, "loto_6_49")
        c = recommend_optimal_config(gk, int(pool_size))
        # Ensemble-ul intră în semnătură: scoringul de producție e blend-ul, nu
        # doar `scorer`. Fără el, o schimbare de compoziție/ponderi (sau de prag
        # de decorelare) ar schimba pool-ul dar ar servi cache WF vechi.
        _ens = c.get("ensemble") or {}
        if isinstance(_ens, dict):
            _ens_sig = ",".join(f"{k}:{round(float(v), 4)}" for k, v in sorted(_ens.items()))
        else:
            _ens_sig = ",".join(sorted(str(m) for m in _ens))
        raw = (f"{c.get('scorer', '?')}|{c.get('sim_depth_pct', 0)}|"
               f"{bool(c.get('use_blacklist', False))}|{BENCH_HIT_TARGET}|{_ens_sig}")
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
            ))
    return flat


def _merge_partial_coverage(
    cached: dict,
    flat_new: list[WalkForwardResult],
    meta: dict,
) -> tuple[list[WalkForwardResult], dict]:
    """REUNIUNE (pe `draw_index`) între un cache PARȚIAL şi rularea nouă.

    De ce reuniune şi nu „câştigă cel mai lung": acoperirea parţială NU e garantat
    un prefix contiguu al cozii recente. Paşii se lansează în ordine recent→vechi,
    dar un pas care crapă sau depăşeşte `_WF_STEP_TIMEOUT_S` e SĂRIT
    (`backtesting._wf_worker_step` loghează şi întoarce None), deci două rulări pot
    avea GĂURI diferite: o rulare mai lungă în total poate rata extrageri pe care
    cache-ul le avea. Regula veche („păstrez cache-ul dacă are mai multe extrageri")
    arunca exact felia acoperită doar de cealaltă rulare.

    Contract: cheia de cache fixează (csv_hash, joc, pool, depth, decizie), deci
    intrările pentru acelaşi `draw_index` sunt echivalente — la conflict păstrăm
    rularea NOUĂ. Rezultatul e monoton crescător prin construcţie.
    """
    cached_flat = list(cached.get("flat") or [])
    if not cached_flat:
        return flat_new, meta

    new_idx = {int(getattr(r, "draw_index", -1)) for r in flat_new}
    extra = [r for r in cached_flat if int(getattr(r, "draw_index", -1)) not in new_idx]
    if not extra:
        return flat_new, meta

    extra_idx = {int(getattr(r, "draw_index", -1)) for r in extra}
    merged = sorted(flat_new + extra, key=lambda r: int(getattr(r, "draw_index", -1)))
    meta["n_predictions"] = int(meta.get("n_predictions") or 0) + len(extra_idx)
    meta["n_test_draws"] = len(new_idx | extra_idx)
    meta["partial"] = meta["n_test_draws"] < int(meta.get("n_expected") or 0)
    meta["merged_partial_cache"] = len(extra_idx)
    # Dacă rularea nouă n-a produs nimic (anulare imediată), rezultatul e integral
    # din cache → UI-ul trebuie să vadă from_cache=True, nu o „rulare" fantomă.
    meta["from_cache"] = not flat_new
    logger.info(
        "[WALK-FWD] Reuniune cu cache-ul parţial: +%d extrageri din cache → %d/%d.",
        len(extra_idx), meta["n_test_draws"], meta.get("n_expected"),
    )
    return merged, meta


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
    _log_stale_wf_cache_once()
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
            # bugetului WF din UI n-ar avea niciun efect). La final REUNIM rularea
            # nouă cu cache-ul pe `draw_index` (vezi `_merge_partial_coverage`) —
            # acoperirile parțiale sunt cozi RECENTE ale aceleiași ferestre, dar pot
            # avea găuri diferite (pași crăpați/timeout), deci „cel mai lung îl
            # conține pe cel mai scurt" NU e garantat.
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

    # Cache PARȚIAL existent → REUNIUNE, nu „câștigă cel mai lung": nicio extragere
    # deja validată nu se pierde, iar acoperirea se acumulează între sesiuni chiar
    # dacă rularea curentă a fost oprită devreme (buget/anulare) sau a sărit pași.
    if cached is not None:
        flat, meta = _merge_partial_coverage(cached, flat, meta)

    # Save cache (rezultatul reunit ⊇ cache → suprascriem; scriere atomică anti-corupere
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
    omnius_ticket: list[int] | None = None,  # PARAMETRU MORT — vezi docstring
) -> tuple[list[WalkForwardResult], dict]:
    """Istoric hits Pool 2 fără walk-forward onest.

    Folosește ACELEAȘI extrageri ca WF Pool 1, dar pool-ul + wheel-ul CURENT
    (generat azi). Rapid (secunde): nu regenerează pipeline-ul la fiecare pas.
    Informativ pentru plasa de siguranță — NU înlocuiește validarea WF Pool 1.

    ⚠️ `omnius_ticket` e IGNORAT COMPLET: biletul OMNIUS a fost scos din UI și din
    walk-forward (2026-07), iar corpul funcției nu-l atinge nicăieri. NU-l pasa
    crezând că influențează rezultatul — nu o face. E păstrat DOAR ca poziția a
    6-a să nu crape apelanții vechi (înainte pasau `[]` pozițional). De șters din
    semnătură când nu mai există niciun call-site cu 6 argumente în istoric/repo.
    """
    from loto_enterprise.core.backtesting import LotoBacktester

    if not flat_reference or not pool_numbers:
        return [], {"retrospective": True, "n_test_draws": 0, "partial": False}

    bt = LotoBacktester(df_source, game_type=game_type)
    draw_n = int({"6/49": 6, "5/40": 5, "joker": 5}.get(game_type, 6))
    pool_set = {int(x) for x in pool_numbers}
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
                ))
        else:
            flat_out.append(WalkForwardResult(
                draw_index=di,
                draw_date=str(dd) if dd else None,
                variant=[],
                hits=hits_union,
                hits_union=hits_union,
                target_draw_date=str(dd) if dd else None,
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


def _stale_wf_cache_files() -> list[Path]:
    """Cache-urile WF scrise de versiuni VECHI de CACHE_VERSION (inaccesibile azi)."""
    if not CACHE_DIR.exists():
        return []
    prefix_now = f"walk_forward_{CACHE_VERSION}_"
    return sorted(
        f for f in CACHE_DIR.glob("walk_forward_*.pkl")
        if not f.name.startswith(prefix_now)
    )


def purge_stale_wf_cache(dry_run: bool = True) -> dict:
    """Inventariază (şi opţional şterge) cache-urile WF de la versiuni VECHI.

    Un bump de CACHE_VERSION schimbă doar NUMELE fişierului de cache: pickle-urile
    versiunilor anterioare rămân pe disc la nesfârşit, permanent inaccesibile — iar
    `CACHE_DIR` fiind o cale relativă (`bench_results/`, deci ÎN repo/OneDrive), se
    şi sincronizează. Funcţia e DRY-RUN implicit: doar LISTEAZĂ şi însumează, ca
    decizia de ştergere să rămână a utilizatorului.

    Spre deosebire de `clear_walk_forward_cache()`, NU atinge versiunea curentă.

    Args:
        dry_run: True (implicit) = doar raportează; False = şterge efectiv.

    Returns:
        {"n_files", "bytes", "mb", "files" (list[str]), "deleted" (bool),
         "n_deleted", "version": CACHE_VERSION}
    """
    files = _stale_wf_cache_files()
    total = 0
    for f in files:
        try:
            total += f.stat().st_size
        except OSError:
            pass
    n_deleted = 0
    if not dry_run:
        for f in files:
            try:
                f.unlink()
                n_deleted += 1
            except OSError as exc:
                logger.warning("[WALK-FWD] nu pot şterge %s: %s", f.name, exc)
    return {
        "n_files": len(files),
        "bytes": total,
        "mb": round(total / (1024 * 1024), 1),
        "files": [f.name for f in files],
        "deleted": not dry_run,
        "n_deleted": n_deleted,
        "version": CACHE_VERSION,
    }


_stale_cache_logged = False


def _log_stale_wf_cache_once() -> None:
    """Anunţă O SINGURĂ DATĂ pe proces cât spaţiu ocupă cache-urile WF vechi.

    Pur informativ — NU şterge nimic (vezi `purge_stale_wf_cache`). Eşecurile sunt
    înghiţite: igiena de disc nu are voie să pice un walk-forward.
    """
    global _stale_cache_logged
    if _stale_cache_logged:
        return
    _stale_cache_logged = True
    try:
        info = purge_stale_wf_cache(dry_run=True)
        if info["n_files"]:
            logger.info(
                "[WALK-FWD] %d cache-uri WF de la versiuni vechi (≠%s) ocupă %.1f MB în "
                "%s/ — inaccesibile. Curăţare opţională: "
                "walk_forward_adapter.purge_stale_wf_cache(dry_run=False).",
                info["n_files"], CACHE_VERSION, info["mb"], CACHE_DIR,
            )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[WALK-FWD] inventar cache vechi eşuat: %s", exc)


def clear_walk_forward_cache() -> int:
    """Şterge toate cache-urile walk-forward (TOATE versiunile, inclusiv cea curentă);
    returnează numărul de fişiere şterse. Pentru a păstra versiunea curentă și a
    șterge doar reziduurile vechi, folosește `purge_stale_wf_cache(dry_run=False)`."""
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
