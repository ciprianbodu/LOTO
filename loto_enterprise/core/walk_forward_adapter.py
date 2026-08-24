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
      dec_sig = semnătura deciziei (scorer/sim_depth/blacklist/ensemble/target) PLUS
      wheel-ul efectiv (algoritm + garanţia internă a WF) → un Re-Bench care schimbă
      câştigătorul, sau o schimbare de algoritm de wheeling, invalidează automat
      cache-ul. Wheel-ul contează pentru că din numărul de BILETE ies costul şi ROI-ul
      raportate; fără el, raportul afişa cifre calculate pe un wheel care nu mai exista.
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
from loto_enterprise.core.wf_sig import ensemble_sig as _ensemble_sig, lookback_pct

logger = logging.getLogger(__name__)

CACHE_DIR = Path("bench_results")
CACHE_VERSION = "v16"
# Changelog (cea mai nouă prima; bump = invalidare cache walk-forward):
# v16: `_decision_sig` include lookback-ul efectiv (0 din UI = 100%) + serializare
#      stabilă a ensemble-ului (listă {method, weight}, nu str(dict)). Fără lookback
#      în cheie, un slider „ultimele X%” schimba pool-ul de producție dar WF
#      valida tot istoricul din cache.
# v15: score_sum_affinity rescris (nu mai produce pool consecutiv 18–28 pe Joker).
#      Pool-ul GENERAT se schimbă oriunde metoda e membru activ (Joker k11 = solo
#      winner) → pickle-urile v14 validează un pool care nu se mai generează.
# v14: `_decision_sig` include acum WHEEL-ul efectiv (algoritm + garanția internă a WF).
#      Cheia veche fixa doar scorer/ensemble/target, deci trecerea wheeling-ului de la
#      ILP la designurile La Jolla (6/49 pool 12: ~54 → 41 bilete) NU a invalidat nimic:
#      raportul continua să afișeze COSTURI și ROI calculate pe wheel-ul vechi, alături
#      de un Pool 2 regenerat cu cel nou (41.819 bilete vs 31.611 pe aceleași extrageri).
#      Bump-ul de VERSIUNE (peste schimbarea de sig, care ar fi invalidat oricum) e ca
#      pickle-urile v13 să devină vizibile pentru `purge_stale_wf_cache`, altfel ar fi
#      rămas orfane la nesfârșit sub aceeași versiune.
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


# Câte numere se extrag per joc (= `pick`-ul wheel-ului).
_WF_PICK = {"6/49": 6, "5/40": 5, "joker": 5}


def _wf_guarantee(pool_size: int, pick: int | None = None) -> int:
    """Garanția cu care walk-forward-ul regenerează wheel-ul la fiecare pas.

    SURSĂ UNICĂ: valoarea e pasată `run_retroactive_backtest(guarantee=...)` mai jos.
    Nu e garanția din setările UI — WF are propria formulă, independentă de ea.

    Plafonată la `pick - 1` când jocul e cunoscut. `guarantee == pick` e o cerere
    DEGENERATĂ: singurul cover 100% e sistemul complet (5/40 pool 15 → C(15,5) =
    3003 bilete), iar greedy-ul se oprește la 1000 de iterații → 1001 bilete la
    33% acoperire. Formula `max(4, pool//3)` atinge 5 de la pool 15, iar la 5/40 și
    Joker `pick` e tot 5 → degenerare la orice pool ≥ 15. Plafonul e intern (WF își
    alege singur garanția); garanția CERUTĂ DE UTILIZATOR în UI rămâne respectată
    întotdeauna, inclusiv `guarantee == pick` = sistem complet, deliberat.
    """
    g = max(4, int(pool_size) // 3)
    if pick:
        g = min(g, int(pick) - 1)
    return max(1, g)


def _wheel_sig(pool_size: int, game_type: str | None = None) -> str:
    """Semnătura wheel-ului EFECTIV pe care îl va folosi walk-forward-ul.

    WF rulează mereu cu `max_variants=0`, deci rezolvă aceeași ramură ca engine-ul
    (`loto_engine.generate_predictions`): override explicit din env `LOTO_WHEEL_METHOD`
    dacă e setat, altfel `lajolla`.

    DE CE intră în cheia de cache: numărul de BILETE per extragere depinde exclusiv de
    algoritmul de wheeling, iar din el se calculează costul, câștigul net și ROI-ul din
    raport. Fără wheel în cheie, schimbarea ILP → La Jolla (6/49 pool 12: ~54 → 41
    bilete) a servit în continuare cache vechi, iar raportul arăta costuri umflate cu
    ~32% pentru Pool 1, lângă un Pool 2 recalculat cu wheel-ul nou.
    """
    method = os.environ.get("LOTO_WHEEL_METHOD", "").strip().lower() or "lajolla"
    return f"{method}|g{_wf_guarantee(pool_size, _WF_PICK.get(game_type))}"


def _decision_sig(game_type: str, pool_size: int, lookback_percent: float = 100.0) -> str:
    """Semnătură scurtă a deciziei bench (scorer + sim_depth + blacklist + target +
    ensemble + wheel + lookback) pentru (joc, pool).
    """
    try:
        from loto_enterprise.core.method_selector import recommend_optimal_config
        from loto_enterprise.benchmark.decision import BENCH_HIT_TARGET
        gk = {"6/49": "loto_6_49", "5/40": "loto_5_40",
              "joker": "joker_urna1"}.get(game_type, "loto_6_49")
        c = recommend_optimal_config(gk, int(pool_size))
        _ens_sig = _ensemble_sig(c.get("ensemble") or [])
        lb = lookback_pct(lookback_percent)
        raw = (f"{c.get('scorer', '?')}|{c.get('sim_depth_pct', 0)}|"
               f"{bool(c.get('use_blacklist', False))}|{BENCH_HIT_TARGET}|{_ens_sig}|"
               f"{_wheel_sig(pool_size, game_type)}|lb{lb}")
        return hashlib.md5(raw.encode()).hexdigest()[:8]
    except Exception as exc:
        logger.warning(f"[WALK-FWD] decizie bench indisponibilă ({exc}) — "
                       f"semnătură doar pe wheel")
        return "nd" + hashlib.md5(_wheel_sig(pool_size, game_type).encode()).hexdigest()[:6]


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
    dec_sig = _decision_sig(game_type, pool_size, lookback_percent)
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
        guarantee=_wf_guarantee(pool_size, _WF_PICK.get(game_type)),
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
) -> tuple[list[WalkForwardResult], dict]:
    """Istoric hits Pool 2 fără walk-forward onest.

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
